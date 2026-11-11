"""Tests of the capture server (aid/capture_server.py): the pre-check of a cart page sent by the browser extension, served on
127.0.0.1 on a free port and posted to through urllib. No test reaches any address but the server under test, and none calls an AI
service."""
import http.client
import json
import os
import re
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from aid import batch, demo
from aid import capture_server as server_module
from aid.capture_server import MAX_BODY, _line_order, find_po, main, page_host, precheck, serve
from aid.madeup.generate import load_specs, pdf_bytes, po_pages
from aid.pipeline import Deps
from aid.readers.cart import read_cart_html
from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, RetryingWriter, WriterError
from aid.readers.llm_client import ReaderError
from aid.review.app import decide
from aid.review.close import close_case, undo
from aid import runner
from aid.schema import DocValue
from aid.settings import Settings, load_settings
from aid.case import load_case
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps

TOKEN = "made-up-token-for-the-tests"
SHOP = "http://127.0.0.1:7870/testshop/CART02.html"          # the url the extension sends with the test shop's page
# Every request goes straight to the server under test: an opener without the proxies of the environment.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class Running:
    url: str
    server: object
    deps: Deps
    settings: Settings


@contextmanager
def running(tmp_path, madeup_out, testshop=True, settings=None):
    """The capture server on 127.0.0.1 and a free port, in a daemon thread, over deps()'s settings with the made-up token, its PO
    store holding the made-up PO PDFs and the two exports (filed through batch.file_po_dir)."""
    d = deps(tmp_path, madeup_out, settings=settings)
    s = d.settings.model_copy(update={"capture_token": TOKEN, "po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    d = d.model_copy(update={"settings": s})
    server = serve(s, d, "127.0.0.1", 0, testshop)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield Running(f"http://127.0.0.1:{server.server_address[1]}", server, d, s)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def capture_server(tmp_path, madeup_out):
    with running(tmp_path, madeup_out) as r:
        yield r


def post(r: Running, html: str, url: str = SHOP, token: str | None = TOKEN, origin: str | None = None) -> tuple[int, dict]:
    """POST /capture with the page and its url; the status and the JSON answer."""
    headers = {"Content-Type": "application/json"} | ({"X-Aid-Token": token} if token is not None else {})
    headers |= {"Origin": origin} if origin else {}
    request = urllib.request.Request(r.url + "/capture", data=json.dumps({"url": url, "html": html}).encode("utf-8"), headers=headers)
    return _open(request)


def get(r: Running, path: str) -> tuple[int, bytes]:
    status, body = _open(urllib.request.Request(r.url + path), raw=True)
    return status, body


def _open(request, raw=False):
    try:
        with OPENER.open(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        with error:
            body, status = error.read(), error.code
    return status, body if raw else json.loads(body)


def raw_request(r: Running, method: str, path: str, headers: dict, body: bytes | None = None):
    """A request sent as written, for the paths urllib would change and the bodies it would send; the response's status, headers
    and body."""
    connection = http.client.HTTPConnection("127.0.0.1", r.server.server_address[1], timeout=30)
    try:
        connection.putrequest(method, path, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def page(madeup_out, name="CART02.html", **replace) -> str:
    """A made-up cart page, with each text of `replace` (a dict of old: new) replaced."""
    html = (madeup_out / name).read_text(encoding="utf-8")
    for old, new in replace.get("texts", {}).items():
        assert old in html
        html = html.replace(old, new)
    return html


def case_folders(r: Running) -> list[str]:
    return sorted(p.name for p in r.settings.cases_root.iterdir() if (p / "case.json").is_file())


def captures(r: Running) -> list[str]:
    return sorted(p.name for p in (r.settings.cases_root / "captures").iterdir())


# --- the pre-check of the made-up carts ---

def test_cart02_gives_differences_with_the_missing_line_and_the_price(capture_server, madeup_out):
    status, answer = post(capture_server, page(madeup_out))
    assert status == 200
    assert (answer["verdict"], answer["status"], answer["po_number"]) == ("differences", "review", "250202")
    texts = [entry["text"] for entry in answer["lines"] if entry["kind"] != "match"]
    assert texts == ["regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder (prijsverschil boven de drempel)",
                     "regel 3 (KM-2003): kon niet in de winkelwagen worden geplaatst"]
    price = next(entry for entry in answer["lines"] if entry["kind"] == "flag" and entry["field"] == "price")
    assert (price["line_id"], price["item_code"], price["po_value"], price["doc_value"]) == ("2", "KM-2002", "40.00", "41.00")
    assert price["reason"] == "Prijsverschil boven de drempel"
    missing = next(entry for entry in answer["lines"] if entry["field"] == "line_presence" and entry["kind"] == "flag")
    assert (missing["line_id"], missing["item_code"], missing["reason"]) == ("3", "KM-2003", "Kon niet in de winkelwagen worden geplaatst")
    assert [entry["line_id"] for entry in answer["lines"]] == sorted((entry["line_id"] for entry in answer["lines"]), key=int)
    assert answer["message"] == "Inkooporder 250202: 1 regel komt overeen, 1 verschil en 1 regel niet in de winkelwagen"
    assert answer["review_url"] == f"http://localhost:7860/?case={answer['case_id']}"
    folder = capture_server.settings.cases_root / answer["case_id"]
    result = json.loads((folder / "steps" / "08_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "review" and (folder / "dispositions.csv").is_file()
    meta = json.loads((folder / "capture.json").read_text(encoding="utf-8"))
    assert set(meta) == {"sha256", "cart_digest", "host", "page"} and meta["host"] == "127.0.0.1"
    assert Path(meta["page"]).parent == capture_server.settings.cases_root / "captures" and Path(meta["page"]).is_file()


def test_cart01_gives_match_and_a_closed_case(capture_server, madeup_out):
    status, answer = post(capture_server, page(madeup_out, "CART01.html"))
    assert status == 200 and (answer["verdict"], answer["status"], answer["po_number"]) == ("match", "closed", "250201")
    assert answer["message"] == "Winkelwagen komt overeen met inkooporder 250201"
    assert {entry["kind"] for entry in answer["lines"]} == {"match"}


def test_a_price_within_the_thresholds_closes_the_case_and_says_what_was_prepared(capture_server, madeup_out):
    """KM-2102 at 4.15 instead of 4.10: 1.2 percent and 0.50 euro on the line, within SETTINGS' thresholds (2 percent, 25 euro), so
    R04 prepares the PO line's price and the article's, and the cart counts as matching."""
    status, answer = post(capture_server, page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"}))
    assert status == 200 and (answer["verdict"], answer["status"]) == ("match", "closed")
    flags = [entry for entry in answer["lines"] if entry["kind"] == "flag"]
    assert [entry["text"] for entry in flags] == [
        "regel 2 (KM-2102): prijs op de inkooporder: wijziging voorbereid van 4.10 naar 4.15, zie de zaak (prijs binnen de drempel)"]
    assert answer["message"] == "Winkelwagen komt overeen met inkooporder 250201; 1 regel op de inkooporder voorbereid"


class _DoneWriter:
    """A writer that makes every change: the prepared writer's entry, marked done."""
    name = "done"

    def __init__(self, folder):
        self.prepared = PreparedWriter(folder)

    def apply(self, op, case_id, rule_id, evidence, now):
        return self.prepared.apply(op, case_id, rule_id, evidence, now).model_copy(update={"status": "done"})


def test_a_write_that_was_made_says_the_po_was_updated(capture_server, madeup_out, tmp_path):
    capture_server.server.deps = capture_server.deps.model_copy(update={"writer": _DoneWriter(tmp_path / "done")})
    status, answer = post(capture_server, page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"}))
    assert status == 200 and answer["verdict"] == "match"
    assert [entry["text"] for entry in answer["lines"] if entry["kind"] == "flag"] == [
        "regel 2 (KM-2102): prijs op de inkooporder bijgewerkt van 4.10 naar 4.15 (prijs binnen de drempel)"]
    assert answer["message"] == "Winkelwagen komt overeen met inkooporder 250201; 1 regel op de inkooporder bijgewerkt"


def test_the_answer_is_in_english_when_the_settings_say_so(tmp_path, madeup_out):
    with running(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"language": "en"})) as r:
        status, answer = post(r, page(madeup_out))
    texts = [entry["text"] for entry in answer["lines"] if entry["kind"] != "match"]
    assert texts == ["line 2 (KM-2002): price 41.00 in the cart, 40.00 on the purchase order (price difference above the threshold)",
                     "line 3 (KM-2003): could not be placed in the cart"]
    assert answer["message"] == "Purchase order 250202: 1 line matches, 1 difference and 1 line not in the cart"


def test_an_extra_line_shows_as_an_extra_line(capture_server, madeup_out):
    """A cart line the PO does not have: the routing's generic "cart: for the buyer" gives way to the comparison's "extra line"."""
    extra = '<tr><td>KM-2099</td><td>Extra</td><td>1</td><td>st</td><td>€ 1,00</td><td>1</td><td>€ 1,00</td></tr>\n    </tbody>'
    status, answer = post(capture_server, page(madeup_out, "CART01.html", texts={"</tbody>": extra}))
    assert status == 200 and answer["verdict"] == "differences"
    entry = next(entry for entry in answer["lines"] if entry["kind"] == "flag")
    assert entry["text"] == "regel 4 (KM-2099): extra regel, niet op de inkooporder" and entry["reason"] == "Extra regel"


# --- the duplicates ---

def test_the_same_page_twice_answers_from_the_same_case(capture_server, madeup_out):
    first = post(capture_server, page(madeup_out))[1]
    second = post(capture_server, page(madeup_out))[1]
    assert second == first
    assert case_folders(capture_server) == [first["case_id"]] and len(captures(capture_server)) == 1


def test_a_changed_invisible_part_answers_from_the_same_case(capture_server, madeup_out):
    """A comment and a timestamp outside the table change the page's bytes, not its cart: the cart digest finds the case."""
    first = post(capture_server, page(madeup_out))[1]
    commented = post(capture_server, page(madeup_out, texts={"<body>": "<body><!-- rendered 09:00:01 -->"}))[1]
    stamped = post(capture_server, page(madeup_out, texts={"</body>": "<p>Bijgewerkt om 09:00:02</p></body>"}))[1]
    assert commented["case_id"] == stamped["case_id"] == first["case_id"]
    assert case_folders(capture_server) == [first["case_id"]] and len(captures(capture_server)) == 1


def test_a_failed_case_is_not_reused(capture_server, madeup_out):
    """A run that failed answers "failed"; the same page sent again opens a new case (a second later: the clock stands still)."""
    calls = []

    def failing(path, kind):
        calls.append(path)
        raise ReaderError("made-up reader failure")
    healthy = capture_server.server.deps
    capture_server.server.deps = healthy.model_copy(update={"reader": failing})
    failed = post(capture_server, page(madeup_out))[1]
    capture_server.server.deps = healthy
    again = post(capture_server, page(madeup_out))[1]
    assert failed["verdict"] == "failed" and calls
    assert again["verdict"] == "differences" and again["case_id"] != failed["case_id"]
    assert again["case_id"] == "250202_20261112T090001"
    assert post(capture_server, page(madeup_out))[1]["case_id"] == again["case_id"]


# --- finding the PO ---

def test_a_page_without_a_stated_po_is_found_by_its_lines(capture_server, madeup_out):
    status, answer = post(capture_server, page(madeup_out, texts={"<p>Uw referentie: 250202</p>": ""}))
    assert status == 200 and answer["po_number"] == "250202" and answer["verdict"] == "differences"


def test_a_cart_of_no_stored_po_gives_no_po(capture_server, madeup_out):
    html = page(madeup_out, texts={"<p>Uw referentie: 250202</p>": "", "KM-2001": "XX-0001", "KM-2002": "XX-0002"})
    status, answer = post(capture_server, html)
    assert status == 200 and (answer["verdict"], answer["po_number"]) == ("no_po", None)
    assert answer["case_id"].startswith("unknown_") and case_folders(capture_server) == [answer["case_id"]]
    assert answer["message"] == "Geen inkooporder gevonden voor deze winkelwagen"


class _Store:
    """A store of PO versions by number, with the numbers find_by_lines gives; a PO as it stands is its last version (rewritten by
    Task 40: find_po asks the store for the PO as it stands)."""

    def __init__(self, found, versions):
        self.found, self.held = found, versions

    def as_it_stands(self, number):
        held = self.held.get(number, [])
        return held[-1] if held else None

    def find_by_lines(self, doc):
        return self.found


def _version(supplier, day):
    return SimpleNamespace(supplier=supplier, version_date=day)


def test_several_candidates_take_the_supplier_then_the_newest_date(madeup_out):
    cart = read_cart_html(madeup_out / "CART02.html").model_copy(update={"po_reference": DocValue()})
    assert cart.supplier.value == "S22" and cart.po_reference.value is None
    revised = _Store(["5", "4"], {"5": [_version("S22", "2026-10-01"), _version("S22", "2026-11-05")],
                                  "4": [_version("S22", "2026-11-01")]})
    assert find_po(cart, revised) == "5"                                   # the PO as it stands counts, not its first version
    store = _Store(["1", "2", "3", "4"], {"1": [_version("S01", "2026-12-01")], "2": [_version("S22", None)],
                                          "3": [_version("S22", "2026-10-01")], "4": [_version("S22", "2026-11-01")]})
    assert find_po(cart, store) == "4"
    assert find_po(cart, _Store(["2", "3"], store.held)) == "3"                  # a version without a date comes last
    assert find_po(cart, _Store(["1", "9"], {"1": [_version("S01", None)], "9": [_version(None, None)]})) == "1"
    assert find_po(cart, _Store(["4"], store.held)) == "4" and find_po(cart, _Store([], {})) is None
    stated = read_cart_html(madeup_out / "CART02.html")
    assert find_po(stated, _Store(["4"], store.held | {"250202": [_version("S22", None)]})) == "250202"
    assert find_po(stated, _Store(["4"], store.held)) == "4"                    # a stated PO the store does not hold


def test_the_version_of_a_cart_case_comes_from_its_own_step_01(capture_server, madeup_out, tmp_path):
    """CART01 is checked against the PO as it stands, the look that CART01_export.csv gave PO 250201, which reads as version 1.
    A PDF of PO 250201 that prints version 1, with other items, is filed after it. The same page sent again is answered from its
    case, and the item codes of the answer come from the version that the case's own step 01 holds, the look, and not from the PDF
    version of the same number. Only when step 01 cannot be read do they come from the PO as it stands, now that PDF."""
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"})
    first = post(capture_server, html)[1]
    store = capture_server.deps.store
    [look] = store.candidates("250201", "cart")
    assert (look.version, look.document.reader) == (1, "export:CART01_export.csv")
    spec = next(spec for spec in load_specs(REPO_ROOT / "aid" / "madeup" / "spec") if spec.case_id == "MU03")
    pages = po_pages(spec, spec.po.versions[0])                                   # MU03's lines, version 1, under PO number 250201
    next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = "Inkooporder 250201"
    pdf = tmp_path / "250201.pdf"
    pdf.write_bytes(pdf_bytes(pages, pdf.name))
    assert store.add(pdf).version == 1 and store.versions("250201")[0].document.lines[1].item_code.value == "KM-0302"
    again = post(capture_server, html)[1]
    assert again == first and again["case_id"] == first["case_id"]
    assert [entry["text"] for entry in again["lines"] if entry["kind"] == "flag"] == [
        "regel 2 (KM-2102): prijs op de inkooporder: wijziging voorbereid van 4.10 naar 4.15, zie de zaak (prijs binnen de drempel)"]
    assert {entry["item_code"] for entry in again["lines"]} == {"KM-2101", "KM-2102", "KM-2103"}
    (capture_server.settings.cases_root / first["case_id"] / "steps" / "01_po_versions.json").write_text("{", encoding="utf-8")
    status, damaged = post(capture_server, html)
    assert status == 200 and damaged["case_id"] == first["case_id"]
    assert {entry["item_code"] for entry in damaged["lines"]} == {"KM-0301", "KM-0302", "KM-0303"}


def test_the_version_of_a_cart_case_on_a_po_known_from_exports_comes_from_its_own_step_01(capture_server, madeup_out, tmp_path):
    """Rule 11 for the usual pre-check (ruling E9): PO 250201 is known from exports only. After CART01's case, the next export of the
    PO changes the item codes of two lines. The same page sent again is answered from its case, with the item codes of the look its
    own step 01 holds, not those of the PO as it stands. When step 01 cannot be read, the answer takes the PO as it stands, here a
    look filed after a PDF of the PO: its item codes, not the PDF's."""
    store = capture_server.deps.store
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"})
    first = post(capture_server, html)[1]
    assert store.versions("250201") == [] and {entry["item_code"] for entry in first["lines"]} == {"KM-2101", "KM-2102", "KM-2103"}

    def exported(name: str, codes: dict[str, str]) -> Path:
        path = tmp_path / name
        text = (madeup_out / "CART01_export.csv").read_text(encoding="utf-8")
        for old, new in codes.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
        return path

    [later] = store.from_export(exported("later.csv", {"KM-2101": "KM-2191", "KM-2102": "KM-2192"}), {})
    assert store.as_it_stands("250201") == later and later.document.lines[0].item_code.value == "KM-2191"
    again = post(capture_server, html)[1]
    assert again == first                                       # its own step 01: KM-2101, KM-2102 and KM-2103
    spec = next(spec for spec in load_specs(REPO_ROOT / "aid" / "madeup" / "spec") if spec.case_id == "MU03")
    pages = po_pages(spec, spec.po.versions[0])                                   # a PDF of PO 250201 with MU03's lines
    next(row for row in pages[0] if row.ref == "po_reference").texts[0].text = "Inkooporder 250201"
    pdf = tmp_path / "250201.pdf"
    pdf.write_bytes(pdf_bytes(pages, pdf.name))
    store.add(pdf)
    [newest] = store.from_export(exported("newest.csv", {"KM-2101": "KM-2181", "KM-2102": "KM-2182", "KM-2103": "KM-2183"}), {})
    v1 = store.root / "250201" / "v1" / "po.json"
    os.utime(store.root / "250201" / "e3" / "po.json", (v1.stat().st_mtime + 60, v1.stat().st_mtime + 60))   # filed after the PDF
    assert store.as_it_stands("250201") == newest
    (capture_server.settings.cases_root / first["case_id"] / "steps" / "01_po_versions.json").write_text("{", encoding="utf-8")
    status, damaged = post(capture_server, html)
    assert status == 200 and damaged["case_id"] == first["case_id"]
    assert {entry["item_code"] for entry in damaged["lines"]} == {"KM-2181", "KM-2182", "KM-2183"}


def test_a_case_level_reason_is_a_review_entry(tmp_path, madeup_out):
    """With first_cases_review 5 on a fresh cases root the first cart of a supplier goes to the buyer as a whole: one entry of kind
    review, without a line."""
    with running(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"first_cases_review": 5})) as r:
        status, answer = post(r, page(madeup_out, "CART01.html"))
    assert status == 200 and (answer["verdict"], answer["status"]) == ("differences", "review")
    review = [entry for entry in answer["lines"] if entry["kind"] == "review"]
    assert review == [{"line_id": "", "item_code": None, "field": None, "kind": "review", "po_value": None, "doc_value": None,
                       "reason": "Nieuwe leverancier", "text": "eerste zaken van deze leverancier gaan naar de inkoper"}]
    assert answer["message"] == "Inkooporder 250201: 3 regels komen overeen en 1 reden voor de inkoper"


# --- the routes and their refusals ---

def test_a_page_without_a_cart_table_is_400_and_kept(capture_server):
    status, answer = post(capture_server, "<html><body><p>Uw winkelwagen is leeg</p></body></html>")
    kept = captures(capture_server)
    assert status == 400 and len(kept) == 1 and kept[0].startswith("nocart_")
    assert answer == {"error": f"no cart table in {kept[0].removeprefix('nocart_')}"}
    log = (capture_server.settings.cases_root / "capture.log").read_text(encoding="utf-8")
    assert str(capture_server.settings.cases_root / "captures" / kept[0]) in log and case_folders(capture_server) == []


def test_a_missing_or_wrong_token_is_401(capture_server, madeup_out):
    assert post(capture_server, page(madeup_out), token="wrong") == (401, {"error": "missing or wrong token"})
    assert post(capture_server, page(madeup_out), token=None)[0] == 401
    log = (capture_server.settings.cases_root / "capture.log").read_text(encoding="utf-8")
    assert TOKEN not in log and "wrong" not in log.replace("missing or wrong token", "")
    assert not (capture_server.settings.cases_root / "captures").exists()


def test_a_body_that_is_not_the_two_strings_is_400(capture_server):
    for body in (b"not json", b'["url", "html"]', b'{"url": "x"}', b'{"url": 1, "html": "x"}'):
        status, _, answer = raw_request(capture_server, "POST", "/capture", {"X-Aid-Token": TOKEN, "Content-Length": str(len(body))}, body)
        assert status == 400 and b"JSON object" in answer


def test_a_body_over_5_mb_is_413_before_it_is_read(capture_server):
    status, _, answer = raw_request(capture_server, "POST", "/capture", {"X-Aid-Token": TOKEN, "Content-Length": str(MAX_BODY + 1)})
    assert status == 413 and json.loads(answer)["error"].startswith("the page is larger than")


def test_health_and_unknown_paths(capture_server):
    assert get(capture_server, "/health") == (200, b'{"ok": true}')
    assert get(capture_server, "/capture")[0] == 404 and get(capture_server, "/")[0] == 404
    assert raw_request(capture_server, "POST", "/other", {"X-Aid-Token": TOKEN, "Content-Length": "0"})[0] == 404


def test_the_test_shop_serves_the_made_up_pages_by_name_only(capture_server):
    status, body = get(capture_server, "/testshop/CART02.html")
    assert status == 200 and body == (server_module.TESTSHOP / "CART02.html").read_bytes()
    for path in ("/testshop/../testshop/CART02.html", "/testshop/%2e%2e/settings.example.json", "/testshop/sub/CART02.html",
                 "/testshop/CART02_export.csv", "/testshop/MISSING.html", "/testshop/", "/testshop/.html"):
        assert raw_request(capture_server, "GET", path, {})[0] == 404, path


def test_the_test_shop_is_off_without_testshop(tmp_path, madeup_out):
    with running(tmp_path, madeup_out, testshop=False) as r:
        assert get(r, "/testshop/CART02.html")[0] == 404 and get(r, "/health")[0] == 200


def test_the_preflight_echoes_only_an_extension_origin(capture_server):
    status, headers, _ = raw_request(capture_server, "OPTIONS", "/capture", {"Origin": "chrome-extension://abc",
                                                                             "Access-Control-Request-Method": "POST"})
    assert status == 204 and headers["Access-Control-Allow-Origin"] == "chrome-extension://abc"
    assert "X-Aid-Token" in headers["Access-Control-Allow-Headers"]
    status, headers, _ = raw_request(capture_server, "OPTIONS", "/capture", {"Origin": "https://example.com"})
    assert status == 204 and "Access-Control-Allow-Origin" not in headers


def test_a_long_host_and_a_lone_surrogate_are_still_saved(capture_server, madeup_out):
    html = page(madeup_out, texts={"<h2>Winkelwagen</h2>": "<h2>Winkelwagen \ud800</h2>"})
    status, answer = post(capture_server, html, url="https://" + "a" * 100 + ".example/cart")
    assert status == 200 and answer["verdict"] == "differences"
    saved = captures(capture_server)
    assert saved == [f"20261112T090000_{'a' * 60}.html"]
    assert "Winkelwagen ?" in (capture_server.settings.cases_root / "captures" / saved[0]).read_text(encoding="utf-8")


def test_the_host_is_reduced_for_the_file_name():
    assert page_host("https://shop.example.com:8443/cart?x=1") == "shop.example.com"
    assert page_host("http://[::1]:7870/") == "1"
    assert page_host("file:///home/cart.html") == "page" and page_host("not a url") == "page" and page_host("http://[") == "page"
    assert page_host("http://sh_op!.example.com/") == "shop.example.com"
    assert page_host("http://" + "b" * 70 + ".com/") == "b" * 60


def test_an_error_of_the_pre_check_is_500_and_logged_without_detail(capture_server, madeup_out, monkeypatch):
    def broken(html, url, deps, settings):
        raise RuntimeError("made-up internal detail")
    monkeypatch.setattr(server_module, "precheck", broken)
    status, answer = post(capture_server, page(madeup_out))
    assert status == 500 and "made-up internal detail" not in json.dumps(answer) and "RuntimeError" not in json.dumps(answer)
    log = (capture_server.settings.cases_root / "capture.log").read_text(encoding="utf-8")
    assert "RuntimeError: made-up internal detail" in log and "Traceback" in log


def test_an_error_outside_the_pre_check_is_one_line_in_the_log(capture_server, monkeypatch, capsys):
    """A client that goes away while the answer is written, or any error of a handler: one line in capture.log, nothing on stderr."""
    def broken(self):
        raise ConnectionResetError("made-up disconnect")
    monkeypatch.setattr(server_module._Handler, "do_GET", broken)
    with pytest.raises((http.client.HTTPException, ConnectionError)):
        raw_request(capture_server, "GET", "/health", {})
    log = (capture_server.settings.cases_root / "capture.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and log[0].endswith("ended: ConnectionResetError: made-up disconnect")
    assert capsys.readouterr().err == "" and server_module._Handler.timeout == 60


def test_one_line_per_capture_goes_to_stderr(capture_server, madeup_out, capsys):
    answer = post(capture_server, page(madeup_out, "CART01.html"))[1]
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and re.fullmatch(rf"\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d 127\.0\.0\.1 {answer['case_id']} match", err[0])
    assert TOKEN not in err[0]


def test_a_case_of_the_same_po_in_the_same_second_opens_a_second_later(capture_server, madeup_out):
    """The clock stands still: a second cart of the same PO opens its case one second later, without waiting."""
    first = post(capture_server, page(madeup_out))[1]["case_id"]
    second = post(capture_server, page(madeup_out, texts={"<td>3</td>": "<td>4</td>"}))[1]["case_id"]
    assert (first, second) == ("250202_20261112T090000", "250202_20261112T090001")


# --- the review round of 9 November ---

def texts_of(answer, kinds=("flag", "uncompared", "review")):
    return [entry["text"] for entry in answer["lines"] if entry["kind"] in kinds]


def test_the_new_supplier_rule_keeps_each_lines_text_and_adds_one_review_entry(tmp_path, madeup_out):
    with running(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"first_cases_review": 5})) as r:
        answer = post(r, page(madeup_out))[1]
    assert answer["verdict"] == "differences"
    assert texts_of(answer) == ["eerste zaken van deze leverancier gaan naar de inkoper",
                                "regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder (verschil)",
                                "regel 3 (KM-2003): kon niet in de winkelwagen worden geplaatst"]
    assert answer["message"] == "Inkooporder 250202: 1 regel komt overeen, 1 verschil, 1 regel niet in de winkelwagen en 1 reden voor de inkoper"


def test_an_r10_reason_reads_as_its_base_rule_in_the_settings_language(tmp_path, madeup_out):
    with running(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"authorization_limit": Decimal("50")})) as r:
        answer = post(r, page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"}))[1]
    flags = texts_of(answer, ("flag",))
    assert flags == ["regel 2 (KM-2102): prijs op de inkooporder: wijziging voorbereid van 4.10 naar 4.15, zie de zaak "
                     "(prijs binnen de drempel)"]
    assert not any("R04" in text or "re-approval" in text for text in texts_of(answer))


def test_a_reason_without_a_label_is_a_fixed_sentence(capture_server, madeup_out):
    """A case cut off after its writes (its result removed) is finished as review with a reason the labels do not know."""
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"})
    first = post(capture_server, html)[1]
    (capture_server.settings.cases_root / first["case_id"] / "steps" / "08_result.json").unlink()
    again = post(capture_server, html)[1]
    assert again["case_id"] == first["case_id"] and again["verdict"] == "differences"
    assert "zie de zaak voor de reden" in texts_of(again, ("review",)) and not any("write log" in t for t in texts_of(again))


def test_a_write_in_a_case_in_review_says_what_was_prepared(capture_server, madeup_out):
    answer = post(capture_server, page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15", "€ 6,20": "€ 7,00"}))[1]
    assert (answer["verdict"], answer["status"]) == ("differences", "review")
    assert texts_of(answer, ("flag",)) == [
        "regel 2 (KM-2102): prijs op de inkooporder: wijziging voorbereid van 4.10 naar 4.15, zie de zaak (prijs binnen de drempel)",
        "regel 3 (KM-2103): prijs 7.00 in de winkelwagen, 6.20 op de inkooporder (prijsverschil boven de drempel)"]
    assert answer["message"] == "Inkooporder 250201: 1 regel komt overeen en 1 verschil; 1 regel op de inkooporder voorbereid"


def test_a_line_presence_match_says_present_once(capture_server, madeup_out):
    answer = post(capture_server, page(madeup_out))[1]
    assert next(e["text"] for e in answer["lines"] if e["kind"] == "match" and e["field"] == "line_presence") == "regel 1 (KM-2001): aanwezig"


def test_an_uncompared_value_is_listed_and_counted(capture_server, madeup_out):
    answer = post(capture_server, page(madeup_out, texts={"€ 12,50": "op aanvraag"}))[1]
    uncompared = [entry for entry in answer["lines"] if entry["kind"] == "uncompared"]
    assert uncompared and uncompared[0]["text"].startswith("regel 1 (KM-2001): prijs niet vergeleken (")
    assert "1 waarde niet vergeleken" in answer["message"]


def test_the_line_ids_sort_as_numbers_after_the_case_entries():
    assert sorted(["10", "2", "", "1, 3"], key=_line_order) == ["", "1, 3", "2", "10"]


def _decide_all(r, case_id, decision):
    case = load_case(r.settings.cases_root, case_id)
    result = json.loads((case.root / "steps" / "08_result.json").read_text(encoding="utf-8"))
    for item in result["review_items"]:
        decide(case, item["item_id"], decision)
    close_case(case, r.deps)


def test_a_case_the_buyer_closed_with_keep_still_answers_differences(capture_server, madeup_out):
    first = post(capture_server, page(madeup_out))[1]
    _decide_all(capture_server, first["case_id"], "keep")
    again = post(capture_server, page(madeup_out))[1]
    assert again["case_id"] == first["case_id"] and (again["status"], again["verdict"]) == ("closed", "differences")
    assert texts_of(again, ("flag",))[0] == ("regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder (prijsverschil "
                                             "boven de drempel); besluit van de inkoper: waarde van de inkooporder gehouden")


def test_a_case_the_buyer_closed_with_confirm_answers_match(capture_server, madeup_out):
    first = post(capture_server, page(madeup_out))[1]
    _decide_all(capture_server, first["case_id"], "confirm")
    again = post(capture_server, page(madeup_out))[1]
    assert (again["status"], again["verdict"]) == ("closed", "match")
    assert texts_of(again, ("flag",)) == [
        "regel 2 (KM-2002): prijs op de inkooporder: wijziging voorbereid van 40.00 naar 41.00, zie de zaak; besluit van de inkoper: "
        "waarde van de winkelwagen overgenomen",
        "regel 3 (KM-2003): kon niet in de winkelwagen worden geplaatst; besluit van de inkoper: waarde van de winkelwagen overgenomen"]


def test_a_buyer_closed_case_with_a_line_the_rules_wrote_answers_match(capture_server, madeup_out):
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15", "€ 6,20": "€ 7,00"})
    first = post(capture_server, html)[1]
    _decide_all(capture_server, first["case_id"], "confirm")
    again = post(capture_server, html)[1]
    assert (again["status"], again["verdict"]) == ("closed", "match")
    assert again["message"] == "Winkelwagen komt overeen met inkooporder 250201; 2 regels op de inkooporder voorbereid"


def test_a_buyer_closed_case_without_its_close_record_is_differences(capture_server, madeup_out):
    first = post(capture_server, page(madeup_out))[1]
    _decide_all(capture_server, first["case_id"], "confirm")
    (capture_server.settings.cases_root / first["case_id"] / "steps" / "09_close.json").unlink()
    assert post(capture_server, page(madeup_out))[1]["verdict"] == "differences"
    import aid.review.app
    import aid.review.close
    assert server_module._review_decisions is aid.review.app._decisions        # the review page's own readers, no copied names
    assert server_module._close_records is aid.review.close._records


def _items(r, case_id):
    case = load_case(r.settings.cases_root, case_id)
    return case, json.loads((case.root / "steps" / "08_result.json").read_text(encoding="utf-8"))["review_items"]


def test_a_confirmed_line_whose_write_was_undone_is_a_difference(capture_server, madeup_out, tmp_path):
    capture_server.server.deps = capture_server.deps.model_copy(update={"writer": _DoneWriter(tmp_path / "done")})
    first = post(capture_server, page(madeup_out))[1]
    case, items = _items(capture_server, first["case_id"])
    for item in items:
        decide(case, item["item_id"], "confirm")
    close_case(case, capture_server.server.deps)
    entry = next(json.loads(line) for line in (case.root / "write_log.jsonl").read_text(encoding="utf-8").splitlines()
                 if json.loads(line)["op"]["kind"] == "po_line_price")
    undo(case, entry["entry_id"], capture_server.server.deps)
    again = post(capture_server, page(madeup_out))[1]
    assert (again["status"], again["verdict"]) == ("closed", "differences")


def test_a_keep_after_an_earlier_prepared_confirm_is_a_difference(capture_server, madeup_out):
    first = post(capture_server, page(madeup_out))[1]
    case, items = _items(capture_server, first["case_id"])
    price = next(item["item_id"] for item in items if item["field"] == "price")
    line = next(item["item_id"] for item in items if item["field"] == "line_presence")
    decide(case, price, "confirm")
    assert close_case(case, capture_server.deps).status == "review"            # the confirm is prepared, the line still open
    decide(case, price, "keep")
    decide(case, line, "confirm")
    assert close_case(case, capture_server.deps).status == "closed"
    again = post(capture_server, page(madeup_out))[1]
    assert (again["status"], again["verdict"]) == ("closed", "differences")


class _FlakyExact:
    """Exact that refuses the first `failures` calls, then makes every change."""
    name = "exact"

    def __init__(self, folder, failures):
        self.done, self.failures = _DoneWriter(folder), failures

    def apply(self, op, case_id, rule_id, evidence, now):
        if self.failures > 0:
            self.failures -= 1
            raise WriterError("made-up: Exact not reachable")
        return self.done.apply(op, case_id, rule_id, evidence, now)


def test_a_write_left_for_the_buyer_then_made_then_undone_is_a_difference(capture_server, madeup_out, tmp_path):
    """The first close's write falls back to "apply by hand" (waiting for the buyer), the second close makes it, the review page
    undoes it: the waiting entry settles nothing and is not counted."""
    writer = RetryingWriter(_FlakyExact(tmp_path / "exact", 2), PreparedWriter(tmp_path / "prepared"))
    d = capture_server.deps.model_copy(update={"writer": writer})
    capture_server.server.deps = d
    first = post(capture_server, page(madeup_out))[1]
    case, items = _items(capture_server, first["case_id"])
    for item in items:
        decide(case, item["item_id"], "confirm")
    close_case(case, d)
    second = close_case(case, d)
    made = next(e for e in second.write_entries if e.status == "done" and e.op.kind == "po_line_price")
    closed = post(capture_server, page(madeup_out))[1]
    assert closed["verdict"] == "match" and closed["message"].endswith("; 1 regel op de inkooporder bijgewerkt")
    undo(case, made.entry_id, d)
    again = post(capture_server, page(madeup_out))[1]
    assert (again["status"], again["verdict"]) == ("closed", "differences")
    assert "bijgewerkt" not in again["message"] and "voorbereid" not in again["message"]


def test_a_confirm_is_settled_by_a_write_the_rules_made_and_keep_never(madeup_out):
    """close_case writes nothing for a confirm whose change the rules made already: that write settles the confirmed flag. The set
    of made writes is the review page's own (aid.review.close._made)."""
    import aid.review.close
    from aid.comparison import Flag
    from aid.execution.ops import WriteOp
    from aid.execution.writer import WriteLogEntry
    assert server_module._made is aid.review.close._made and server_module._target is aid.review.close._target
    flag = Flag(flag_id="F1", field="price", po_line_ids=["2"], doc_line_ids=["2"], po_value=None, doc_value=None, detail="different")
    entry = WriteLogEntry(entry_id="e1", case_id="c", op=WriteOp(kind="po_line_price", po_number="250202", version=1, line_id="2",
                                                                old_value="40.00", new_value="41.00"),
                          status="done", previous="40.00", new="41.00", rule_id="R04", evidence={}, timestamp="2026-11-12T09:00:00")
    writes = server_module._Writes([entry], set())
    assert server_module._settled(flag, "confirm", writes) and server_module._settled(flag, None, writes)
    assert not server_module._settled(flag, "keep", writes)
    assert not server_module._settled(flag, "confirm", server_module._Writes([], set()))
    waiting = entry.model_copy(update={"status": "prepared", "error": "WriterError: made-up"})
    assert not server_module._settled(flag, "confirm", server_module._Writes([waiting], set()))


def test_an_undone_write_is_a_difference_again(capture_server, madeup_out, tmp_path):
    capture_server.server.deps = capture_server.deps.model_copy(update={"writer": _DoneWriter(tmp_path / "done")})
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"})
    first = post(capture_server, html)[1]
    assert first["verdict"] == "match"
    case = load_case(capture_server.settings.cases_root, first["case_id"])
    entry = json.loads((case.root / "write_log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    undo(case, entry["entry_id"], capture_server.server.deps)
    again = post(capture_server, html)[1]
    assert again["verdict"] == "differences" and "bijgewerkt" not in again["message"]
    assert texts_of(again, ("flag",)) == [
        "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (wijziging ongedaan gemaakt)"]


def test_the_same_bytes_on_another_day_answer_from_the_same_case(capture_server, madeup_out):
    first = post(capture_server, page(madeup_out))[1]
    capture_server.server.deps = capture_server.deps.model_copy(update={"clock": lambda: datetime(2026, 11, 13, 9, 0)})
    again = post(capture_server, page(madeup_out))[1]
    assert again["case_id"] == first["case_id"] and case_folders(capture_server) == [first["case_id"]]
    assert len(captures(capture_server)) == 1
    changed = post(capture_server, page(madeup_out, texts={"<body>": "<body><!-- next day -->"}))[1]
    assert changed["case_id"] == "250202_20261113T090000"           # the cart digest answers within its day only


@pytest.mark.parametrize("texts", [{"€ 41,00": "€ 41,50"}, {"<td>st</td><td>€ 41,00": "<td>doos</td><td>€ 41,00"},
                                   {"<p>Uw referentie: 250202</p>": ""}])
def test_the_cart_digest_holds_the_price_the_unit_and_the_stated_po(capture_server, madeup_out, texts):
    first = post(capture_server, page(madeup_out))[1]
    other = post(capture_server, page(madeup_out, texts=texts))[1]
    assert other["case_id"] != first["case_id"]


def test_an_error_after_the_page_was_saved_leaves_no_copy(capture_server, madeup_out, monkeypatch):
    def broken(cart, store):
        raise RuntimeError("made-up store failure")
    monkeypatch.setattr(server_module, "find_po", broken)
    assert post(capture_server, page(madeup_out))[0] == 500
    assert captures(capture_server) == [] and case_folders(capture_server) == []


def test_a_content_length_with_leading_zeros_is_read_as_its_number(capture_server):
    status, _, _ = raw_request(capture_server, "POST", "/capture", {"X-Aid-Token": TOKEN, "Content-Length": "0" * 5000 + "1"}, b"x")
    assert status == 400


def test_a_huge_content_length_is_413_and_deep_json_is_400(capture_server):
    status, _, _ = raw_request(capture_server, "POST", "/capture", {"X-Aid-Token": TOKEN, "Content-Length": "9" * 5000})
    assert status == 413
    body = b"[" * 200000 + b"]" * 200000
    status, _, _ = raw_request(capture_server, "POST", "/capture", {"X-Aid-Token": TOKEN, "Content-Length": str(len(body))}, body)
    assert status == 400


def test_the_base_handlers_own_errors_are_lines_in_the_log(capture_server, monkeypatch, capsys):
    """A stalled client (the socket timeout) and a request line http.server refuses: one line each in capture.log."""
    monkeypatch.setattr(server_module._Handler, "timeout", 0.3)
    connection = http.client.HTTPConnection("127.0.0.1", capture_server.server.server_address[1], timeout=10)
    connection.putrequest("POST", "/capture")
    connection.putheader("X-Aid-Token", TOKEN)
    connection.putheader("Content-Length", "100")
    connection.endheaders(b"{")
    with pytest.raises((http.client.HTTPException, ConnectionError)):
        connection.getresponse()
    connection.close()
    assert raw_request(capture_server, "GARBAGE", "/", {})[0] in (400, 501)
    log = (capture_server.settings.cases_root / "capture.log").read_text(encoding="utf-8")
    assert "timed out" in log and len(log.splitlines()) == 2 and capsys.readouterr().err == ""


def test_cors_on_a_post_answer_and_a_moz_extension_preflight(capture_server, madeup_out):
    request = urllib.request.Request(capture_server.url + "/capture", data=json.dumps({"url": SHOP, "html": page(madeup_out)}).encode(),
                                     headers={"X-Aid-Token": TOKEN, "Origin": "chrome-extension://abc"})
    with OPENER.open(request, timeout=30) as response:
        assert response.headers["Access-Control-Allow-Origin"] == "chrome-extension://abc"
    _, headers, _ = raw_request(capture_server, "OPTIONS", "/capture", {"Origin": "moz-extension://abc"})
    assert headers["Access-Control-Allow-Origin"] == "moz-extension://abc"


def test_a_server_without_a_token_refuses_an_empty_one(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    server = serve(d.settings, d, "127.0.0.1", 0, False)
    assert d.settings.capture_token is None
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        r = Running(f"http://127.0.0.1:{server.server_address[1]}", server, d, d.settings)
        assert post(r, "<html></html>", token="")[0] == 401 and post(r, "<html></html>", token=None)[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_the_running_file_marks_a_capture_run_and_is_removed_after_it(capture_server, madeup_out, monkeypatch):
    seen = []
    real = server_module.run_case

    def watching(case, d):
        seen.append(json.loads((case.root / runner.RUNNING_FILE).read_text(encoding="utf-8")))
        now, root = capture_server.deps.clock(), capture_server.settings.cases_root
        assert runner._without_result(root, now) == []                               # the runner leaves the case alone
        assert runner._without_result(root, now + runner.RUNNING_FRESH) == [case.case_id]       # and resumes it when it is old
        return real(case, d)
    monkeypatch.setattr(server_module, "run_case", watching)
    answer = post(capture_server, page(madeup_out))[1]
    assert set(seen[0]) == {"pid", "started"}
    assert not (capture_server.settings.cases_root / answer["case_id"] / runner.RUNNING_FILE).exists()

    def broken(case, d):
        raise RuntimeError("made-up run failure")
    monkeypatch.setattr(server_module, "run_case", broken)
    assert post(capture_server, page(madeup_out, texts={"€ 41,00": "€ 41,50"}))[0] == 500
    assert not list(capture_server.settings.cases_root.glob(f"*/{runner.RUNNING_FILE}"))


def test_the_runner_resumes_a_case_whose_running_file_is_old(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out)
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    d = d.model_copy(update={"settings": s})
    case = server_module.open_capture_case(s.cases_root, madeup_out / "CART02.html", "250202", d.clock())
    running_file = case.root / runner.RUNNING_FILE
    running_file.write_text(json.dumps({"pid": 1, "started": d.clock().isoformat()}), encoding="utf-8")
    assert runner._without_result(s.cases_root, d.clock()) == []
    later = d.clock() + runner.RUNNING_FRESH
    assert runner._without_result(s.cases_root, later) == [case.case_id]
    earlier = d.clock() - timedelta(minutes=1)                  # a start a little in the future (clock skew) is a run in progress
    assert runner._without_result(s.cases_root, earlier) == []
    assert runner._without_result(s.cases_root, d.clock() - runner.RUNNING_FRESH) == [case.case_id]


def test_a_pass_checks_running_with_the_time_of_the_check(tmp_path, madeup_out):
    """A capture that starts while a pass scans is not run by that pass: the check reads the clock when it runs, not when the pass
    began."""
    d = deps(tmp_path, madeup_out)
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    start = datetime(2026, 11, 12, 9, 0)
    late = start + runner.RUNNING_FRESH + timedelta(minutes=5)          # the server's start, long after the pass began
    times = iter([start])
    d = d.model_copy(update={"settings": s, "clock": lambda: next(times, late + timedelta(seconds=10))})
    case = server_module.open_capture_case(s.cases_root, madeup_out / "CART02.html", "250202", late)
    (case.root / runner.RUNNING_FILE).write_text(json.dumps({"pid": 1, "started": late.isoformat()}), encoding="utf-8")
    runner._pass(runner._Loop(d))                                  # one pass of a loop over these Deps
    assert not (case.root / "steps" / "08_result.json").exists()


def test_the_runner_leaves_a_running_case_without_dispositions_and_unreported(tmp_path, madeup_out):
    """The window between the server's run_case and its record_dispositions: no recovery and no report while .running is fresh."""
    d = deps(tmp_path, madeup_out)
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    d = d.model_copy(update={"settings": s})
    answer = precheck(page(madeup_out), SHOP, d, s)
    folder = s.cases_root / answer.case_id
    (folder / "dispositions.csv").unlink()
    (folder / runner.RUNNING_FILE).write_text(json.dumps({"pid": 1, "started": d.clock().isoformat()}), encoding="utf-8")
    now = d.clock()
    assert runner._without_dispositions(s.cases_root, now) == []
    assert runner._without_dispositions(s.cases_root, now + runner.RUNNING_FRESH) == [answer.case_id]
    state = {"last_sent": None, "reported": [], "skipped": []}
    runner._report_captures(s.cases_root, state, now)
    assert state["reported"] == []
    runner._report_captures(s.cases_root, state, now + runner.RUNNING_FRESH)
    assert state["reported"] == [answer.case_id]


def test_the_servers_resume_of_a_found_case_writes_the_running_file(capture_server, madeup_out, monkeypatch):
    first = post(capture_server, page(madeup_out))[1]
    folder = capture_server.settings.cases_root / first["case_id"]
    (folder / "steps" / "08_result.json").unlink()
    (folder / "dispositions.csv").unlink()
    seen = []
    real = server_module.run_case

    def watching(case, d):
        seen.append((case.root / runner.RUNNING_FILE).is_file())
        return real(case, d)
    monkeypatch.setattr(server_module, "run_case", watching)
    again = post(capture_server, page(madeup_out))[1]
    assert again["case_id"] == first["case_id"] and seen == [True] and not (folder / runner.RUNNING_FILE).exists()


def test_the_runner_reports_a_capture_case_in_its_digest_once(tmp_path, madeup_out):
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"digest": "per_case"}))
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    d = d.model_copy(update={"settings": s, "mailer": None})
    answer = precheck(page(madeup_out), SHOP, d, s)
    runner.run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    digest = (s.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "voorcontrole" in digest and "250202" in digest
    state = json.loads((s.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert state["captures"] == [answer.case_id] and state["reported"] == []
    (s.cases_root / "digests" / "2026-11-12.txt").unlink()
    runner.run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    assert not (s.cases_root / "digests" / "2026-11-12.txt").exists()          # reported once


def test_main_binds_127_0_0_1_unless_host_is_given(tmp_path, monkeypatch, capsys):
    calls = []

    class Stub:
        server_address = ("127.0.0.1", 7870)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake(settings, d, host, port, testshop, **watch):
        calls.append((host, port, testshop))
        return Stub()
    monkeypatch.setattr(server_module, "serve", fake)
    path = _settings_file(tmp_path, capture_token=TOKEN)
    assert main(["--settings", str(path)]) == 0 and main(["--settings", str(path), "--host", "0.0.0.0", "--port", "8000"]) == 0
    assert calls == [("127.0.0.1", 7870, False), ("0.0.0.0", 8000, False)]


def test_a_second_server_on_a_port_in_use_ends_with_exit_2(capture_server, tmp_path, capsys):
    port = capture_server.server.server_address[1]
    assert server_module._CaptureServer.allow_reuse_address == (os.name != "nt")
    assert main(["--settings", str(_settings_file(tmp_path, capture_token=TOKEN)), "--port", str(port)]) == 2
    assert capsys.readouterr().err.startswith("aid.capture_server: ")


def test_precheck_runs_without_the_server(tmp_path, madeup_out):
    """precheck itself: the page saved, read, its case opened and run."""
    d = deps(tmp_path, madeup_out)
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    answer = precheck(page(madeup_out), SHOP, d.model_copy(update={"settings": s}), s)
    assert answer.verdict == "differences" and answer.case_id == "250202_20261112T090000"


# --- the command ---

def _settings_file(tmp_path, **values) -> Path:
    data = json.loads((Path("aid") / "settings.example.json").read_text(encoding="utf-8"))
    data |= {"reader": "parser", "cases_root": str(tmp_path / "cases"), "po_store_root": str(tmp_path / "store"),
             "watched_folder": str(tmp_path / "inbox")} | values
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_main_refuses_an_empty_token(tmp_path, capsys):
    assert main(["--settings", str(_settings_file(tmp_path))]) == 2
    assert main(["--settings", str(_settings_file(tmp_path, capture_token="  "))]) == 2
    err = capsys.readouterr().err
    assert err.count("capture_token is empty") == 2 and len(err.strip().splitlines()) == 2


def test_main_refuses_a_bad_port_and_a_missing_settings_file(tmp_path, capsys):
    path = _settings_file(tmp_path, capture_token=TOKEN)
    assert main(["--settings", str(path), "--port", "70000"]) == 2
    assert main(["--settings", str(tmp_path / "missing.json")]) == 2
    err = capsys.readouterr().err
    assert "port" in err.splitlines()[0] and "missing.json" in err.splitlines()[1]
    assert len(err.strip().splitlines()) == 2 and all(line.startswith("aid.capture_server: ") for line in err.strip().splitlines())
    assert TOKEN not in err


# --- the demo ---

def test_the_demo_settings_hold_the_made_up_token_and_the_texts_name_the_capture_server(tmp_path):
    out = tmp_path / "demo"
    out.mkdir()
    settings = load_settings(demo.write_settings(out))
    assert settings.capture_token == "made-up-token-for-the-test-shop"
    (out / "summary.csv").write_text("case_id,status\nCART02,review\n", encoding="utf-8")
    for text in (demo.prepared_text(out), demo.summary_text(out, "parser")):
        assert f"python3 -m aid.capture_server --settings {out / 'settings.json'} --testshop" in text
        assert "http://127.0.0.1:7870/testshop/CART02.html" in text and "aid/extension/README.md" in text
    hint = demo.prepared_text(out)                         # the digest after every case: the settings tab first, the file as the other way
    assert "\"Na elke zaak\" under \"Dagoverzicht\" on the settings tab \"Instellingen\" of the review page" in hint
    assert f"python3 -m aid.review --settings {out / 'settings.json'}; or set \"digest\": \"per_case\" in settings.json" in hint


@pytest.mark.parametrize("ids", [{"ms_tenant_id": "00000000-0000-0000-0000-000000000001", "ms_client_id": "00000000-0000-0000-0000-000000000002"},
                                 {}])
def test_the_capture_server_asks_no_model_whatever_the_reader_says(tmp_path, monkeypatch, ids):
    """With reader "llm" the server still reads with the cart reader and decides nothing with a model: it starts without a sign-in
    for Copilot, with or without the tenant and the client, and its Deps hold no model decider."""
    from aid.decider import NoDecider
    from aid.pipeline import read_with_parser
    seen = {}

    class Stub:
        server_address = ("127.0.0.1", 7870)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake(settings, d, host, port, testshop, **watch):
        seen.update(settings=settings, deps=d)
        return Stub()
    monkeypatch.setattr(server_module, "serve", fake)
    assert main(["--settings", str(_settings_file(tmp_path, capture_token=TOKEN, reader="llm", **ids))]) == 0
    assert isinstance(seen["deps"].decider, NoDecider) and seen["deps"].reader is read_with_parser
    assert seen["settings"].reader == "llm"


# --- the settings file read again when it changes (Task 29) ---

PRICE_ABOVE = {"€ 4,10": "€ 4,25"}           # KM-2102 at 4.25 instead of 4.10: 3.7 percent, above SETTINGS' 2 percent


@contextmanager
def watching(tmp_path, madeup_out, rebuild=None):
    """The capture server over a settings file of its own, which it reads again when the file changes. Its Deps are rebuilt from the
    new settings by `rebuild`, or else as the test's Deps were built but with a writer of their own, so that Deps the server rebuilt
    are told from Deps that only carry other settings. Yields the running server, the settings file and the Deps rebuilt so far."""
    d = deps(tmp_path, madeup_out)
    s = d.settings.model_copy(update={"capture_token": TOKEN, "po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(s.model_dump(mode="json"), indent=2), encoding="utf-8")
    s = load_settings(path)
    d = d.model_copy(update={"settings": s})
    rebuilt = []

    def marked(new: Settings) -> Deps:
        made = rebuild(new) if rebuild is not None else d.model_copy(update={"settings": new, "writer": PreparedWriter(tmp_path / "rebuilt")})
        rebuilt.append(made)
        return made

    server = serve(s, d, "127.0.0.1", 0, False, settings_file=path, rebuild=marked)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield Running(f"http://127.0.0.1:{server.server_address[1]}", server, d, s), path, rebuilt
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def touch_later(path: Path, seconds: int) -> None:
    """Move the file's modification time on, so that a change within one tick of the clock is seen as a change."""
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + seconds * 1_000_000_000))


def test_raised_thresholds_in_the_settings_file_give_the_same_server_another_verdict(tmp_path, madeup_out):
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        status, first = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE))
        assert status == 200 and (first["verdict"], first["status"]) == ("differences", "review") and rebuilt == []
        data = json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "5"}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        touch_later(path, 1)
        second_page = page(madeup_out, "CART01.html", texts=PRICE_ABOVE | {"<p>Uw referentie: 250201</p>": ""})   # another cart, the same difference
        status, second = post(r, second_page)
        assert status == 200 and (second["verdict"], second["status"]) == ("match", "closed") and second["case_id"] != first["case_id"]
        assert r.server.settings.price_pct == Decimal("5") and r.server.deps.settings.price_pct == Decimal("5")
        # the server called its rebuild, once, and works with the Deps it made: the change of the second cart went to their writer
        assert len(rebuilt) == 1 and r.server.deps is rebuilt[0] and r.server.deps is not r.deps
        assert (tmp_path / "rebuilt" / "prepared_changes.csv").is_file() and not (tmp_path / "prepared_changes.csv").exists()
        assert post(r, second_page)[0] == 200 and len(rebuilt) == 1                          # no change of the file: no rebuild


def test_a_cart_checked_before_the_thresholds_changed_keeps_its_outcome_until_the_cart_itself_changes(tmp_path, madeup_out):
    """What the manual tells the buyer after a save (section 2.7): the same cart is answered from its case, a changed cart is
    checked anew with the new settings."""
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        cart = page(madeup_out, "CART01.html", texts=PRICE_ABOVE)
        first = post(r, cart)[1]
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "5"}, indent=2), encoding="utf-8")
        touch_later(path, 1)
        again = post(r, cart)[1]
        assert (again["case_id"], again["verdict"]) == (first["case_id"], "differences") and r.server.settings.price_pct == Decimal("5")
        changed = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE | {"<p>Uw referentie: 250201</p>": ""}))[1]
        assert changed["case_id"] != first["case_id"] and changed["verdict"] == "match"


def test_a_rebuild_that_raises_keeps_the_settings_and_the_deps_and_logs_one_line_with_the_class_only(tmp_path, madeup_out):
    def refused(new: Settings) -> Deps:
        raise ValueError("made-up: the writer refuses secret-xyz-value")

    with watching(tmp_path, madeup_out, rebuild=refused) as (r, path, rebuilt):
        before, deps_before = r.server.settings, r.server.deps
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "5"}, indent=2), encoding="utf-8")
        touch_later(path, 1)
        status, answer = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE))
        assert status == 200 and answer["verdict"] == "differences"                         # still 2 percent
        assert post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE | {"<p>Uw referentie: 250201</p>": ""}))[0] == 200
        assert r.server.settings is before and r.server.deps is deps_before and rebuilt == []
        log = (r.settings.cases_root / "capture.log").read_text(encoding="utf-8")
        assert len(log.splitlines()) == 1 and "ValueError" in log and "secret-xyz-value" not in log and "made-up" not in log


@pytest.mark.parametrize("damage, named", [(lambda path: path.unlink(), "FileNotFoundError"),
                                           (lambda path: path.write_text('{"price_pct": "secret-xyz-value", ', encoding="utf-8"), "Expecting")])
def test_a_changed_settings_file_that_cannot_be_read_keeps_the_settings_and_logs_one_line(tmp_path, madeup_out, damage, named):
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        before, deps_before = r.server.settings, r.server.deps
        damage(path)
        if path.exists():
            touch_later(path, 1)
        status, answer = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE))
        assert status == 200 and answer["verdict"] == "differences"
        assert post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE | {"<p>Uw referentie: 250201</p>": ""}))[0] == 200
        assert r.server.settings is before and r.server.deps is deps_before and rebuilt == []
        log = (r.settings.cases_root / "capture.log").read_text(encoding="utf-8")
        assert len(log.splitlines()) == 1 and named in log and "secret-xyz-value" not in log and TOKEN not in log


def test_a_file_that_is_not_valid_after_a_valid_change_keeps_the_settings_in_use_not_those_of_the_start(tmp_path, madeup_out):
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        good = json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "5"}
        path.write_text(json.dumps(good, indent=2), encoding="utf-8")
        touch_later(path, 1)
        assert post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE))[1]["verdict"] == "match"
        in_use, deps_in_use = r.server.settings, r.server.deps
        assert in_use.price_pct == Decimal("5") and deps_in_use is rebuilt[0]
        path.write_text(json.dumps(good | {"price_abs": "secret-xyz-value"}, indent=2), encoding="utf-8")
        touch_later(path, 2)
        status, answer = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE | {"<p>Uw referentie: 250201</p>": ""}))
        assert status == 200 and answer["verdict"] == "match"                               # 5 percent still, not the 2 of the start
        assert r.server.settings is in_use and r.server.deps is deps_in_use and len(rebuilt) == 1


def test_a_changed_token_in_the_settings_file_is_the_token_from_then_on(tmp_path, madeup_out):
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"capture_token": "another-made-up-token"}, indent=2),
                        encoding="utf-8")
        touch_later(path, 1)
        assert post(r, page(madeup_out, "CART01.html"))[0] == 401
        assert post(r, page(madeup_out, "CART01.html"), token="another-made-up-token")[0] == 200


def test_a_changed_settings_file_that_is_not_valid_keeps_the_settings_and_logs_one_line_without_a_value(tmp_path, madeup_out):
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        before = r.server.settings
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "secret-xyz-value"}, indent=2), encoding="utf-8")
        touch_later(path, 1)
        status, answer = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE))
        assert status == 200 and answer["verdict"] == "differences"
        status, again = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE | {"<p>Uw referentie: 250201</p>": ""}))
        assert status == 200 and r.server.settings is before
        log = (r.settings.cases_root / "capture.log").read_text(encoding="utf-8")
        assert len(log.splitlines()) == 1 and "price_pct" in log and "secret-xyz-value" not in log and TOKEN not in log


def test_main_gives_the_server_its_settings_file_and_a_rebuild_as_main_builds_the_deps(tmp_path, monkeypatch):
    from aid.decider import NoDecider
    from aid.pipeline import read_with_parser
    seen = {}

    class Stub:
        server_address = ("127.0.0.1", 7870)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake(settings, d, host, port, testshop, **watch):
        seen.update(watch)
        return Stub()
    monkeypatch.setattr(server_module, "serve", fake)
    path = _settings_file(tmp_path, capture_token=TOKEN, reader="parser")
    assert main(["--settings", str(path)]) == 0
    assert seen["settings_file"] == path
    (tmp_path / "other").mkdir()
    rebuilt = seen["rebuild"](load_settings(_settings_file(tmp_path / "other", capture_token=TOKEN, reader="llm",
                                                           ms_tenant_id="00000000-0000-0000-0000-000000000001",
                                                           ms_client_id="00000000-0000-0000-0000-000000000002")))
    assert isinstance(rebuilt.decider, NoDecider) and rebuilt.reader is read_with_parser and rebuilt.settings.reader == "parser"


def test_a_put_back_waiting_on_the_list_is_no_undo_until_it_is_entered(capture_server, madeup_out):
    """The reviewers' sequence (Task 35's review round, K3): a cart case closed with the cart's price confirmed (B01), the change
    ticked as entered on the list and then put back ("Terugdraaien in Exact"). While the put-back waits on the list, the pre-check
    keeps the write and says that the put-back is on the list, never "wijziging ongedaan gemaakt"; once the put-back is ticked as
    entered, the write is undone and the line is a difference again."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared)})
    capture_server.server.deps = d
    first = post(capture_server, page(madeup_out))[1]
    case, items = _items(capture_server, first["case_id"])
    decide(case, next(item["item_id"] for item in items if item["field"] == "price"), "confirm")
    decide(case, next(item["item_id"] for item in items if item["field"] == "line_presence"), "keep")
    close_case(case, d)

    def line_two(answer) -> str:
        [text] = [entry["text"] for entry in answer["lines"] if entry["kind"] == "flag" and entry["line_id"] == "2"]
        return text

    [change] = [change for change in changes(prepared)[0] if change.po_number == "250202"]
    mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    made = "regel 2 (KM-2002): prijs op de inkooporder: wijziging voorbereid van 40.00 naar 41.00, zie de zaak"
    decided = "besluit van de inkoper: waarde van de winkelwagen overgenomen"
    assert line_two(post(capture_server, page(madeup_out))[1]) == f"{made}; {decided}"
    entered = next(one for one in changes(prepared)[0] if one.ids == change.ids)
    assert take_back(d, entered, datetime(2026, 11, 12, 11, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    waiting = post(capture_server, page(madeup_out))[1]
    assert line_two(waiting) == f"{made}; terugzetten staat op de lijst In Exact invoeren; {decided}"
    assert "ongedaan" not in json.dumps(waiting, ensure_ascii=False)
    assert server_module._TEXTS["en"]["put_back"] == "put-back is on the list Enter in Exact"
    [put] = [one for one in changes(prepared)[0] if one.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 12, 12, 0))
    undone = post(capture_server, page(madeup_out))[1]
    assert line_two(undone) == f"regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder (wijziging ongedaan gemaakt); {decided}"


# --- the second review round of Task 35: the pre-check and the list of changes to enter (K10, K12, K15) ---

def _flag_of(answer: dict, line_id: str) -> str:
    [text] = [entry["text"] for entry in answer["lines"] if entry["kind"] == "flag" and entry["line_id"] == line_id]
    return text


def _cart_01(capture_server, madeup_out) -> tuple:
    """CART01 with line 2 at 4,15: R04 prepares 4.10 -> 4.15 on the list and the pre-check is green. (the page, the first answer, the
    Deps with the prepared writer, the list's folder)."""
    prepared = capture_server.settings.cases_root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared)})
    capture_server.server.deps = d
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"})
    first = post(capture_server, html)[1]
    assert first["verdict"] == "match"
    return html, first, d, prepared


def test_a_put_back_of_a_change_asked_again_waits_on_the_list_and_the_pre_check_says_so(capture_server, madeup_out):
    """The checkers' sequence (second review round, K10): CART02's line 2 confirmed by the buyer (B01 40.00 -> 41.00) and ticked; a
    newer change of the line that the buyer drops; then 41.00 asked once more by another document ("already"). "Terugdraaien in Exact"
    makes a put-back that stands open on the list, and until it is entered the pre-check keeps the write with "terugzetten staat op de
    lijst In Exact invoeren", never "wijziging ongedaan gemaakt"; once it is entered, the line is a difference again."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared)})
    capture_server.server.deps = d
    first = post(capture_server, page(madeup_out))[1]
    case, items = _items(capture_server, first["case_id"])
    decide(case, next(item["item_id"] for item in items if item["field"] == "price"), "confirm")
    decide(case, next(item["item_id"] for item in items if item["field"] == "line_presence"), "keep")
    close_case(case, d)
    [change] = [change for change in changes(prepared)[0] if change.po_number == "250202"]
    mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    writer = PreparedWriter(prepared)
    newer = writer.apply(WriteOp(kind="po_line_price", po_number="250202", version=1, line_id="2", old_value="41.00", new_value="42.00"),
                         "250202_20261112T103000", "R04", {}, datetime(2026, 11, 12, 10, 30))
    mark(prepared, [newer.entry_id], "dropped", datetime(2026, 11, 12, 10, 35))
    writer.apply(WriteOp(kind="po_line_price", po_number="250202", version=1, line_id="2", old_value="40.00", new_value="41.00"),
                 "250202_20261112T104000", "B01", {}, datetime(2026, 11, 12, 10, 40))
    assert [change.state for change in changes(prepared)[0] if change.po_number == "250202"] == ["entered", "dropped", "already"]
    entered = next(one for one in changes(prepared)[0] if one.ids == change.ids)
    assert take_back(d, entered, datetime(2026, 11, 12, 11, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    assert [change.state for change in changes(prepared)[0] if change.po_number == "250202"] == ["entered", "dropped", "replaced", "open"]
    made = "regel 2 (KM-2002): prijs op de inkooporder: wijziging voorbereid van 40.00 naar 41.00, zie de zaak"
    decided = "besluit van de inkoper: waarde van de winkelwagen overgenomen"
    waiting = post(capture_server, page(madeup_out))[1]
    assert _flag_of(waiting, "2") == f"{made}; terugzetten staat op de lijst In Exact invoeren; {decided}"
    assert "ongedaan" not in json.dumps(waiting, ensure_ascii=False)
    [put] = [one for one in changes(prepared)[0] if one.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 12, 12, 0))
    undone = post(capture_server, page(madeup_out))[1]
    assert _flag_of(undone, "2") == ("regel 2 (KM-2002): prijs 41.00 in de winkelwagen, 40.00 op de inkooporder (wijziging ongedaan gemaakt); "
                                     f"{decided}")


def test_an_undo_passed_over_on_the_list_leaves_the_write_standing(capture_server, madeup_out):
    """An undo row that the run rules pass over (state undone) undoes its write only when the write's own row was cancelled with it
    (second review round, K10): here the operator undid a ticked change after a later document asked its value again, so the undo
    belongs to that later run, which holds no row of its case and no tick, and is passed over; Exact still holds 4.15, and the pre-check
    keeps the write and stays green."""
    from aid.execution.by_hand import changes, mark
    from aid.execution.writer import undo_entry

    html, first, d, prepared = _cart_01(capture_server, madeup_out)
    [change] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    writer = PreparedWriter(prepared)
    newer = writer.apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.15", new_value="4.20"),
                         "250201_20261112T103000", "R04", {}, datetime(2026, 11, 12, 10, 30))
    mark(prepared, [newer.entry_id], "dropped", datetime(2026, 11, 12, 10, 35))
    writer.apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.10", new_value="4.15"),
                 "250201_20261112T104000", "R04", {}, datetime(2026, 11, 12, 10, 40))
    undo_entry(capture_server.settings.cases_root / first["case_id"], change.ids[0], writer, datetime(2026, 11, 12, 11, 0))
    assert [one.state for one in changes(prepared)[0] if one.kind == "po_line_price" and one.po_number == "250201"] == [
        "entered", "dropped", "already", "undone"]
    again = post(capture_server, html)[1]
    assert again["verdict"] == "match" and "ongedaan" not in json.dumps(again, ensure_ascii=False)
    assert _flag_of(again, "2").startswith("regel 2 (KM-2102): prijs op de inkooporder: wijziging voorbereid van 4.10 naar 4.15")


def test_a_write_cancelled_with_its_undo_before_it_was_entered_is_undone(capture_server, madeup_out):
    """K3's branch for an undo cancelled together with its write (second review round, K15): the operator undoes the case with
    MANUAL 3.10's recipe, undo_case through the prepared writer, before the buyer entered anything; on the list the change and its
    undo cancel each other and Exact still holds 4.10, so the pre-check of the cart at 4,15 says "wijziging ongedaan gemaakt" and is
    not green."""
    from aid.execution.by_hand import changes
    from aid.execution.writer import undo_case

    html, first, d, prepared = _cart_01(capture_server, madeup_out)
    undo_case(capture_server.settings.cases_root / first["case_id"], d.writer, datetime(2026, 11, 12, 10, 0))
    assert {one.state for one in changes(prepared)[0] if one.po_number == "250201"} == {"undone"}
    again = post(capture_server, html)[1]
    assert again["verdict"] != "match"
    assert _flag_of(again, "2") == "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (wijziging ongedaan gemaakt)"


def test_a_write_whose_undo_the_marks_say_exact_holds_is_undone(capture_server, madeup_out):
    """K3's branch for an undo in the state "already" (second review round, K15): the change 4.10 -> 4.15 was ticked, then another
    document asked 4.10 again and that was ticked too, so Exact holds 4.10 by the marks, and a third asked 4.20, which was dropped;
    the operator's undo of the write, which asks 4.10, is "already". The pre-check of the cart at 4,15 says "wijziging ongedaan
    gemaakt" and is not green."""
    from aid.execution.by_hand import changes, mark
    from aid.execution.writer import undo_entry

    html, first, d, prepared = _cart_01(capture_server, madeup_out)
    [change] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    writer = PreparedWriter(prepared)
    back = writer.apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.15", new_value="4.10"),
                        "250201_20261112T103000", "R04", {}, datetime(2026, 11, 12, 10, 30))
    mark(prepared, [back.entry_id], "entered", datetime(2026, 11, 12, 10, 35))
    other = writer.apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.10", new_value="4.20"),
                         "250201_20261112T104000", "R04", {}, datetime(2026, 11, 12, 10, 40))
    mark(prepared, [other.entry_id], "dropped", datetime(2026, 11, 12, 10, 45))
    undo_entry(capture_server.settings.cases_root / first["case_id"], change.ids[0], writer, datetime(2026, 11, 12, 11, 0))
    assert [one.state for one in changes(prepared)[0] if one.kind == "po_line_price" and one.po_number == "250201"] == [
        "entered", "entered", "dropped", "already"]
    again = post(capture_server, html)[1]
    assert again["verdict"] != "match"
    assert _flag_of(again, "2") == "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (wijziging ongedaan gemaakt)"


def test_a_put_back_counts_as_undoing_while_the_list_cannot_be_read(capture_server, madeup_out, monkeypatch):
    """The checkers' sequence (second review round, K12): CART01's change ticked, put back, and the put-back ticked as entered, so Exact
    holds 4.10 again. While prepared_changes.csv or marks.jsonl cannot be read (a PermissionError, as Windows gives for a file another
    program holds), no prepared write counts as made (fourth review round, K30): the line is a difference, "(de lijst In Exact
    invoeren kon niet worden gelezen)", never a write that stands, and the cart is not green. So also while the put-back still waits."""
    import pathlib

    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    html, first, d, prepared = _cart_01(capture_server, madeup_out)
    [change] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    take_back(d, next(one for one in changes(prepared)[0] if one.ids == change.ids), datetime(2026, 11, 12, 11, 0), "nl")
    unread = "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (de lijst In Exact invoeren kon niet worden gelezen)"
    real = pathlib.Path.read_bytes

    def held_by(name: str):
        def read_bytes(self):
            if self.name == name:
                raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
            return real(self)
        return read_bytes

    for name in ("prepared_changes.csv", "marks.jsonl"):                     # the put-back waits: the list unreadable
        monkeypatch.setattr(pathlib.Path, "read_bytes", held_by(name))
        blind = post(capture_server, html)[1]
        monkeypatch.setattr(pathlib.Path, "read_bytes", real)
        assert blind["verdict"] != "match" and _flag_of(blind, "2") == unread, name
    readable = post(capture_server, html)[1]
    assert readable["verdict"] == "match" and "terugzetten staat op de lijst In Exact invoeren" in _flag_of(readable, "2")
    [put] = [one for one in changes(prepared)[0] if one.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 12, 12, 0))
    for name in ("prepared_changes.csv", "marks.jsonl"):                     # the put-back entered: Exact holds 4.10 again
        monkeypatch.setattr(pathlib.Path, "read_bytes", held_by(name))
        blind = post(capture_server, html)[1]
        monkeypatch.setattr(pathlib.Path, "read_bytes", real)
        assert blind["verdict"] == "differences" and _flag_of(blind, "2") == unread, name
    assert post(capture_server, html)[1]["verdict"] == "differences"


# --- the third review round of Task 35: the pre-check judges a write by the list (K19) ---

def _cart_on(capture_server, madeup_out, day: int, html: str | None = None) -> tuple:
    """CART01 at 4,15 captured on that day of November at 9:00, with the prepared writer: (the page, the answer, the Deps). A page of
    another day holds a comment of its own, so that it is a new case, as the same cart shown again on another day is."""
    prepared = capture_server.settings.cases_root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared), "clock": lambda: datetime(2026, 11, day, 9, 0)})
    capture_server.server.deps = d
    html = html or page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"}).replace("</body>", f"<!-- shown {day}-11 -->\n</body>")
    return html, post(capture_server, html)[1], d


def test_a_later_case_of_the_same_cart_is_judged_by_the_put_back_on_the_list(capture_server, madeup_out):
    """The checkers' sequence (third review round, K19): CART01 at 4,15 on 12 November (case A, R04 4.10 -> 4.15), ticked; a newer
    value for the line dropped; the same cart on 13 November (case C) asks 4.10 -> 4.15 again ("already"). The put-back of A's change
    stands and replaces C's row. C's own write log holds no undo, but the pre-check judges C's write by the list: while the put-back
    waits it says "terugzetten staat op de lijst In Exact invoeren", and once the put-back is entered the line is a difference,
    "(wijziging ongedaan gemaakt)", and the cart is not green."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    newer = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.15",
                                                   new_value="4.20"), "250201_20261112T103000", "R04", {}, datetime(2026, 11, 12, 10, 30))
    mark(prepared, [newer.entry_id], "dropped", datetime(2026, 11, 12, 10, 35))
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    assert second["case_id"] != first["case_id"] and second["verdict"] == "match"
    states = [change.state for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    assert states == ["entered", "dropped", "already"]
    entered = next(change for change in changes(prepared)[0] if change.ids == a.ids)
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 13, 10, 0)})
    assert take_back(later, entered, datetime(2026, 11, 13, 10, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    waiting = post(capture_server, html_c)[1]
    assert waiting["case_id"] == second["case_id"]
    assert "terugzetten staat op de lijst In Exact invoeren" in _flag_of(waiting, "2") and "ongedaan" not in json.dumps(waiting)
    [put] = [change for change in changes(prepared)[0] if change.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 13, 11, 0))
    undone = post(capture_server, html_c)[1]
    assert undone["verdict"] != "match"
    assert _flag_of(undone, "2") == "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (wijziging ongedaan gemaakt)"
    assert post(capture_server, html_a)[1]["verdict"] != "match"                # case A's own page, as before


def test_the_same_cart_on_two_days_is_judged_by_the_put_back_of_the_run(capture_server, madeup_out):
    """The checkers' sequence (third review round, K19): the same cart on 12 and 13 November, with no other value in between, is one
    change with the entries of two cases; the tick came before the second capture. The put-back undoes the first case's entry, and
    the second case's pre-check says that it waits, then, once it is entered, "(wijziging ongedaan gemaakt)"."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    [run] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    assert len(run.case_ids) == 2 and run.state == "entered"
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 13, 10, 0)})
    assert take_back(later, run, datetime(2026, 11, 13, 10, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    assert "terugzetten staat op de lijst In Exact invoeren" in _flag_of(post(capture_server, html_c)[1], "2")
    [put] = [change for change in changes(prepared)[0] if change.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 13, 11, 0))
    undone = post(capture_server, html_c)[1]
    assert undone["verdict"] != "match" and "(wijziging ongedaan gemaakt)" in _flag_of(undone, "2")


# --- the fourth review round of Task 35: a replaced row judged by its own put-back (K29), and no prepared write stands while the
# list cannot be read (K30) ---

UNREAD = "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (de lijst In Exact invoeren kon niet worden gelezen)"
UNDONE = "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (wijziging ongedaan gemaakt)"


def _blind(capture_server, monkeypatch, html: str, name: str) -> dict:
    """The answer for the page while the list's file `name` cannot be read (a PermissionError, as Windows gives for a file another
    program holds)."""
    import pathlib

    real = pathlib.Path.read_bytes

    def read_bytes(self):
        if self.name == name:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read_bytes)
    try:
        return post(capture_server, html)[1]
    finally:
        monkeypatch.setattr(pathlib.Path, "read_bytes", real)


def test_a_replaced_row_is_judged_by_the_put_back_that_replaced_it(capture_server, madeup_out):
    """The checkers' sequence (fourth review round, K29): on CART01's line 2, other cases ask 4.10 -> 4.30, 4.30 -> 4.15 (X1),
    4.15 -> 4.30 and 4.10 -> 4.15 (X3), all entered, and 4.10 -> 4.30, which is open, when the cart at 4,15 is captured (case C asks
    4.10 -> 4.15, "already"). X3's put-back replaces C's row and is entered: C is amber with "(wijziging ongedaan gemaakt)". The
    put-back of X1, another change of the same value, waits and is dropped: C stays amber, as Exact holds 4.10 all along; before, the
    newest put-back of that value judged C's write, and C was green."""
    from aid.case import open_case
    from aid.execution.by_hand import changes, mark
    from aid.execution.writer import WriteLog
    from aid.review.hand_list import take_back

    root = capture_server.settings.cases_root
    prepared = root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared)})
    ids = {}
    for hour, (name, old, new, tick) in enumerate((("X0", "4.10", "4.30", True), ("X1", "4.30", "4.15", True),
                                                   ("X2", "4.15", "4.30", True), ("X3", "4.10", "4.15", True),
                                                   ("X4", "4.10", "4.30", False)), 10):
        at = datetime(2026, 11, 10, hour, 0)
        case = open_case(root, "confirmation", [], "250201", at)
        entry = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value=old,
                                                       new_value=new), case.case_id, "R04", {}, at)
        WriteLog(case.root).append(entry)
        ids[name] = entry.entry_id
        if tick:
            mark(prepared, [entry.entry_id], "entered", at + timedelta(minutes=5))

    def change_of(entry_id):
        return next(change for change in changes(prepared)[0] if entry_id in change.ids)

    html, first, _ = _cart_on(capture_server, madeup_out, 11)
    assert first["verdict"] == "match"
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 11, 10, 0)})
    assert take_back(later, change_of(ids["X3"]), datetime(2026, 11, 11, 10, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    [p1] = [change for change in changes(prepared)[0] if change.puts_back == ids["X3"]]
    mark(prepared, p1.ids, "entered", datetime(2026, 11, 11, 10, 5))
    undone = post(capture_server, html)[1]
    assert undone["verdict"] == "differences" and _flag_of(undone, "2") == UNDONE
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 11, 11, 0)})
    assert take_back(later, change_of(ids["X1"]), datetime(2026, 11, 11, 11, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    waiting = post(capture_server, html)[1]
    assert waiting["verdict"] == "differences" and _flag_of(waiting, "2") == UNDONE
    [p2] = [change for change in changes(prepared)[0] if change.puts_back == ids["X1"]]
    mark(prepared, p2.ids, "dropped", datetime(2026, 11, 11, 11, 5))
    dropped = post(capture_server, html)[1]
    assert dropped["verdict"] == "differences" and _flag_of(dropped, "2") == UNDONE


@pytest.mark.parametrize("reask", [False, True])
def test_no_prepared_write_stands_while_the_list_cannot_be_read(capture_server, madeup_out, monkeypatch, reask):
    """The checkers' sequences (fourth review round, K30): CART01 at 4,15 captured on 12 November (case A) and ticked; the same cart on
    13 November (case C), with a dropped 4.20 in between or not; A's change put back, which waits and is then entered. Case C's write
    log holds no undo. While prepared_changes.csv or marks.jsonl cannot be read, neither case is green, the put-back waiting or
    entered: the line shows the difference with "(de lijst In Exact invoeren kon niet worden gelezen)". Before, C was green with
    "wijziging voorbereid" while A was amber. Once the list can be read again, the list decides (K19)."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    for name in ("prepared_changes.csv", "marks.jsonl"):                     # a prepared change ticked or not: not counted made
        blind = _blind(capture_server, monkeypatch, html_a, name)
        assert blind["verdict"] == "differences" and _flag_of(blind, "2") == UNREAD, name
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    if reask:
        op = WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.15", new_value="4.20")
        newer = PreparedWriter(prepared).apply(op, "250201_20261112T103000", "R04", {}, datetime(2026, 11, 12, 10, 30))
        mark(prepared, [newer.entry_id], "dropped", datetime(2026, 11, 12, 10, 35))
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    assert second["verdict"] == "match"
    run = next(change for change in changes(prepared)[0] if a.ids[0] in change.ids)
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 13, 10, 0)})
    assert take_back(later, run, datetime(2026, 11, 13, 10, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    for html in (html_c, html_a):                                             # the put-back waits
        for name in ("prepared_changes.csv", "marks.jsonl"):
            blind = _blind(capture_server, monkeypatch, html, name)
            assert blind["verdict"] == "differences" and _flag_of(blind, "2") == UNREAD, name
    assert "terugzetten staat op de lijst In Exact invoeren" in _flag_of(post(capture_server, html_c)[1], "2")
    [put] = [change for change in changes(prepared)[0] if change.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 13, 11, 0))
    for html in (html_c, html_a):                                             # the put-back entered: Exact holds 4.10 again
        for name in ("prepared_changes.csv", "marks.jsonl"):
            blind = _blind(capture_server, monkeypatch, html, name)
            assert blind["verdict"] == "differences" and _flag_of(blind, "2") == UNREAD, name
        readable = post(capture_server, html)[1]
        assert readable["verdict"] == "differences" and _flag_of(readable, "2") == UNDONE
    assert server_module._TEXTS["en"]["unread"] == "the list Enter in Exact could not be read"


def test_a_replaced_row_is_judged_by_the_put_back_that_took_its_value_out_of_exact(capture_server, madeup_out):
    """The acceptance harness of the fourth review round (seed 7330 and its kind): on CART01's line 2 another case's 4.10 -> 4.15 (X) is
    entered; a change to 4.30 is open when the cart at 4,15 is captured (case C asks 4.10 -> 4.15, "already": Exact holds X's 4.15).
    A newer document then asks 4.15 -> 4.10, which replaces C's row and is dropped; X is put back, and its put-back entered: Exact holds
    4.10. C's row was replaced by that newer document, not by the put-back, yet the put-back of X took C's value out of Exact: C is
    amber with "(wijziging ongedaan gemaakt)", and while the put-back waits, "terugzetten staat op de lijst In Exact invoeren"."""
    from aid.case import open_case
    from aid.execution.by_hand import changes, mark
    from aid.execution.writer import WriteLog
    from aid.review.hand_list import take_back

    root = capture_server.settings.cases_root
    prepared = root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared)})

    def ask(old: str, new: str, at: datetime) -> str:
        case = open_case(root, "confirmation", [], "250201", at)
        entry = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value=old,
                                                       new_value=new), case.case_id, "R04", {}, at)
        WriteLog(case.root).append(entry)
        return entry.entry_id

    x = ask("4.10", "4.15", datetime(2026, 11, 10, 10, 0))
    mark(prepared, [x], "entered", datetime(2026, 11, 10, 10, 5))
    ask("4.15", "4.30", datetime(2026, 11, 10, 11, 0))
    html, first, _ = _cart_on(capture_server, madeup_out, 11)
    assert first["verdict"] == "match"
    newer = ask("4.15", "4.10", datetime(2026, 11, 11, 9, 30))
    mark(prepared, [newer], "dropped", datetime(2026, 11, 11, 9, 35))
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 11, 10, 0)})
    entered = next(change for change in changes(prepared)[0] if x in change.ids)
    assert take_back(later, entered, datetime(2026, 11, 11, 10, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    states = [(change.new_value, change.rule_id, change.state) for change in changes(prepared)[0] if change.po_number == "250201"
              and change.kind == "po_line_price"]
    assert states[2:] == [("4.15", "R04", "replaced"), ("4.10", "R04", "dropped"), ("4.10", "undo", "open")]
    assert "terugzetten staat op de lijst In Exact invoeren" in _flag_of(post(capture_server, html)[1], "2")
    [put] = [change for change in changes(prepared)[0] if change.puts_back == x]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 11, 10, 5))
    undone = post(capture_server, html)[1]
    assert undone["verdict"] == "differences" and _flag_of(undone, "2") == UNDONE
