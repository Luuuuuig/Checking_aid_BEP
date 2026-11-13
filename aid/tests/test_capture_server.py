"""Tests of the capture server (aid/capture_server.py): the pre-check of a cart page sent by the browser extension, served on
127.0.0.1 on a free port and posted to through urllib. No test reaches any address but the server under test, and none calls an AI
service."""
import http.client
import inspect
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aid import batch, demo
from aid import capture_server as server_module
from aid.capture_server import MAX_BODY, _line_order, find_po, main, page_host, precheck, precheck_deps, serve
from aid.madeup.generate import PoLine as MadeUpPoLine
from aid.madeup.generate import load_specs, pdf_bytes, po_pages
from aid.messaging import FileMailer
from aid.pipeline import GUESSED_CART, GUESSED_PO, Deps, run_case
from aid.readers.cart import read_cart_html
from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, RetryingWriter, WriterError
from aid.readers.llm_client import ReaderError
from aid.review.app import decide, list_open_cases
from aid.review.page import close_text, decision_text, offered_cases, selection_from_query
from aid.review import app as app_module
from aid.review import close as close_module
from aid.review.close import close_case, undo
from aid.review.i18n import STRINGS, reason_label
from aid import runner
from aid.settings import Settings, load_settings
from aid.case import Case, load_case, open_case
from aid.sources.capture import ALREADY_SENT, BY_ITEMS, NOT_SURE, cart_order, open_capture_case
from aid.sources.mailbox import FOUND_BY_LINES
from aid.tryout import cart_html, confirmation_pdf, write_cart
from aid.tryout_mark import TRYOUT_OUTSIDE, tryout_case
from aid.tests.conftest import REPO_ROOT, SETTINGS, deps

TOKEN = "made-up-token-for-the-tests"
FILED = datetime(2026, 10, 1, 9, 0)                           # when the store of running() counts as filed
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
    store holding the made-up PO PDFs and the two exports (filed through batch.file_po_dir). The store counts as filed on 1 October
    2026, before every made-up day of these tests: whether a tick of the list came before or after a look at an order was filed
    decides what a cart is compared with (Task 43), and must not rest on the day the suite runs."""
    d = deps(tmp_path, madeup_out, settings=settings)
    s = d.settings.model_copy(update={"capture_token": TOKEN, "po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    for filed in (tmp_path / "store").glob("*/*/po.json"):
        os.utime(filed, (FILED.timestamp(), FILED.timestamp()))
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


# The fixed last line of the bar of a match and of differences (ruling Q14), and the title of a guess that waits (ruling Q21).
NOT_COMPARED = {"nl": "Niet vergeleken: levertijd, minimale afname of verpakkingseenheid, verzendkosten en leveringsvoorwaarden.",
                "en": "Not compared: delivery time, minimum or pack quantity, delivery costs and delivery terms."}
GUESS_TITLE = "Let op: kijk na of dit de goede inkooporder is (check that this is the right purchase order)"
GUESS_OPENINGS = ("De assistent weet niet zeker welke inkooporder", "De inkooporder die het best bij deze winkelwagen past",
                  "The assistant is not sure which purchase order", "The purchase order that fits this cart best")


def post(r: Running, html: str, url: str = SHOP, token: str | None = TOKEN, origin: str | None = None,
         whole: bool = False) -> tuple[int, dict]:
    """POST /capture with the page and its url; the status and the JSON answer. Every answer of a pre-check is checked for its
    fixed parts (_checked) and given without them, so that a test names the lines of its own case; with `whole` it is given as
    the server sent it."""
    headers = {"Content-Type": "application/json"} | ({"X-Aid-Token": token} if token is not None else {})
    headers |= {"Origin": origin} if origin else {}
    request = urllib.request.Request(r.url + "/capture", data=json.dumps({"url": url, "html": html}).encode("utf-8"), headers=headers)
    status, answer = _open(request)
    if status == 200:
        return status, answer if whole else _checked(answer)
    assert set(answer) == {"error"} and not any(said in json.dumps(answer) for said in (*NOT_COMPARED.values(), GUESS_TITLE))
    return status, answer


def _checked(answer: dict) -> dict:
    """The answer of a pre-check without its fixed last line and its title, after the check that each is there exactly when it
    should be. The last line, what a cart check does not compare, stands under a match and under differences, a guess among them,
    as an entry of the kind every script of the extension lists, and nowhere else. The title stands on a guess that still waits,
    which is an answer whose first entry is the sentence of a guess, and on no other answer."""
    lines = answer["lines"]
    noted = [entry for entry in lines if entry["text"] in NOT_COMPARED.values()]
    if answer["verdict"] in ("match", "differences"):
        assert noted == lines[-1:], answer
        assert {name: value for name, value in lines[-1].items() if name != "text"} == {
            "line_id": "", "item_code": None, "field": None, "kind": "flag", "po_value": None, "doc_value": None, "reason": "not compared"}
        lines = lines[:-1]
    else:
        assert noted == [], answer
    waits = bool(lines) and lines[0]["kind"] == "review" and lines[0]["text"].startswith(GUESS_OPENINGS)
    assert ("title" in answer) == waits and answer.get("title", GUESS_TITLE) == GUESS_TITLE, answer
    assert not waits or answer["verdict"] == "differences", answer
    return {name: value for name, value in answer.items() if name != "title"} | {"lines": lines}


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


def raw_request(r: Running, method: str, path: str, headers: dict, body: bytes | None = None, host: bool = True):
    """A request sent as written, for the paths urllib would change and the bodies it would send; the response's status, headers
    and body. With `host` false the request carries no Host header but those of `headers`, a list of values for more than one."""
    connection = http.client.HTTPConnection("127.0.0.1", r.server.server_address[1], timeout=30)
    try:
        connection.putrequest(method, path, skip_host=not host, skip_accept_encoding=True)
        for name, values in headers.items():
            for value in values if isinstance(values, list) else [values]:
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
    assert set(meta) == {"sha256", "cart_digest", "lines_digest", "host", "page"} and meta["host"] == "127.0.0.1"
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


def test_the_bar_says_what_a_cart_check_does_not_compare(tmp_path, madeup_out):
    """Ruling Q14: one fixed last line under a match and under differences, also under a guess, in the settings' language: what a
    cart check does not compare. It is an entry of the kind "flag" without a line, the one kind that every script of the extension
    lists for both verdicts, so that a script loaded before this round shows it too; it stands after every entry of the case and
    is counted in no sentence. The grey bar, a failed pre-check and a refusal have no such line."""
    for language in ("nl", "en"):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            folder = tmp_path / language
            exported(r, folder, "today.csv", {"260001": OWN})
            exported(r, folder, "earlier.csv", {"260002": ("KM-9003", "KM-9004")}, at=EIGHT - DAY)
            released(r, folder, "260003", ("KM-9005", "KM-9006"), at=EIGHT - DAY)
            match = post(r, cart_page(), whole=True)[1]
            differences = post(r, cart_page(prices={"KM-9002": "44,00"}), whole=True)[1]
            not_sure = post(r, cart_page(("KM-9003", "KM-9004")), whole=True)[1]
            sent = post(r, cart_page(("KM-9005", "KM-9006"), prices={"KM-9005": "8,00"}), whole=True)[1]
            grey = post(r, cart_page(("KM-9007",)), whole=True)[1]
            assert [answer["verdict"] for answer in (match, differences, not_sure, sent, grey)] == [
                "match", "differences", "differences", "differences", "no_po"]
            note = {"line_id": "", "item_code": None, "field": None, "kind": "flag", "po_value": None, "doc_value": None,
                    "reason": "not compared", "text": NOT_COMPARED[language]}
            for answer in (match, differences, not_sure, sent):
                assert answer["lines"][-1] == note and [entry for entry in answer["lines"] if entry == note] == [note], answer["verdict"]
            assert [entry["kind"] for entry in match["lines"]].count("flag") == 1          # the note alone: a match without a change
            assert match["message"] == {"nl": f"Winkelwagen komt overeen met inkooporder 260001{CHOSEN_NL}",
                                        "en": f"Cart matches purchase order 260001{CHOSEN_EN}"}[language]
            assert not_sure["lines"][0]["text"].startswith(GUESS_OPENINGS) and len(not_sure["lines"]) > 2
            for other in NOT_COMPARED.values():
                assert other not in json.dumps(grey, ensure_ascii=False)
            healthy = r.server.deps

            def failing(path, kind):
                raise ReaderError("made-up reader failure")
            r.server.deps = healthy.model_copy(update={"reader": failing})
            failed = post(r, cart_page(prices={"KM-9001": "2,60"}), whole=True)[1]              # another cart, whose run fails
            r.server.deps = healthy
            assert failed["verdict"] == "failed" and not any(said in json.dumps(failed, ensure_ascii=False) for said in NOT_COMPARED.values())
            again = post(r, cart_page(prices={"KM-9002": "44,00"}), whole=True)[1]           # the same page again: the line stands once
            assert again == differences
            for status, refused in (post(r, "<html><body><p>geen winkelwagen</p></body></html>"), post(r, cart_page(), token="wrong")):
                assert status in (400, 401) and set(refused) == {"error"}


def test_the_title_of_a_guess_that_waits_asks_to_check_the_order(tmp_path, madeup_out):
    """Ruling Q21: the answer of a guess whose result is not closed carries a title, in the two-language form of the extension's
    own titles, which the extension shows in place of its title of the verdict: under Q1 a guess is an everyday bar, and for a
    cart equal to its order "the cart differs" is not so. The verdict stays "differences", so that a script loaded before this
    round keeps its own title. Every other answer has no title: a match, differences on an order found for sure, the grey bar, a
    failed pre-check, and a guess that the buyer closed."""
    for language in ("nl", "en"):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            folder = tmp_path / language
            exported(r, folder, "today.csv", {"260001": OWN})
            exported(r, folder, "earlier.csv", {"260002": ("KM-9003", "KM-9004")}, at=EIGHT - DAY)
            released(r, folder, "260003", ("KM-9005", "KM-9006"), at=EIGHT - DAY)
            not_sure = post(r, cart_page(("KM-9003", "KM-9004")), whole=True)[1]             # equal to its order: nothing differs
            sent = post(r, cart_page(("KM-9005", "KM-9006"), prices={"KM-9005": "8,00"}), whole=True)[1]
            assert not_sure["title"] == sent["title"] == GUESS_TITLE and (not_sure["verdict"], sent["verdict"]) == ("differences", "differences")
            assert [entry["kind"] for entry in not_sure["lines"] if entry["kind"] != "match"] == ["review", "flag"]
            for answer in (post(r, cart_page(), whole=True)[1], post(r, cart_page(prices={"KM-9002": "44,00"}), whole=True)[1],
                           post(r, cart_page(("KM-9007",)), whole=True)[1]):
                assert "title" not in answer, answer["verdict"]
            _decide_all(r, not_sure["case_id"], "keep")                         # the buyer checked the order and closed the case
            closed = post(r, cart_page(("KM-9003", "KM-9004")), whole=True)[1]
            assert (closed["case_id"], closed["status"], closed["verdict"]) == (not_sure["case_id"], "closed", "match") and "title" not in closed
            healthy = r.server.deps

            def failing(path, kind):
                raise ReaderError("made-up reader failure")
            r.server.deps = healthy.model_copy(update={"reader": failing})
            failed = post(r, cart_page(prices={"KM-9001": "2,60"}), whole=True)[1]              # another cart, whose run fails
            guessed = post(r, cart_page(("KM-9003", "KM-9004"), prices={"KM-9003": "0,21"}), whole=True)[1]    # and the cart of a guess
            r.server.deps = healthy
            assert failed["verdict"] == "failed" and "title" not in failed
            assert (guessed["verdict"], guessed["po_number"]) == ("failed", "260002") and "title" not in guessed
            assert json.loads((r.settings.cases_root / guessed["case_id"] / "case.json").read_text(encoding="utf-8"))["po_found_by"] == NOT_SURE
    d = deps(tmp_path / "direct", madeup_out)
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "direct" / "store"})
    batch.file_po_dir(s, madeup_out)
    answer = precheck(page(madeup_out, "CART01.html"), SHOP, d.model_copy(update={"settings": s}), s)
    assert answer.title is None and answer.lines[-1]["text"] == NOT_COMPARED["nl"]


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


# --- finding the PO of a cart: the number the page states, else the one order of today with exactly the cart's items, else a guess
# (Task 41, ruling Q1 of the review round) ---

def test_a_page_without_a_stated_po_is_compared_with_the_order_that_holds_its_items(capture_server, madeup_out):
    """CART02 without its reference line: its order holds one line more than the cart, so the aid is not sure of it. The cart is
    compared with that order as a guess, whatever day the export of the made-up set was filed on."""
    status, answer = post(capture_server, page(madeup_out, texts={"<p>Uw referentie: 250202</p>": ""}))
    assert status == 200 and answer["po_number"] == "250202" and answer["verdict"] == "differences"
    assert load_case(capture_server.settings.cases_root, answer["case_id"]).po_found_by == NOT_SURE
    assert texts_of(answer)[0] == NOT_SURE_NL.format(po="250202")


def test_a_cart_of_no_stored_po_gives_no_po(capture_server, madeup_out):
    html = page(madeup_out, texts={"<p>Uw referentie: 250202</p>": "", "KM-2001": "XX-0001", "KM-2002": "XX-0002"})
    status, answer = post(capture_server, html)
    assert status == 200 and (answer["verdict"], answer["po_number"]) == ("no_po", None)
    assert answer["case_id"].startswith("unknown_") and case_folders(capture_server) == [answer["case_id"]]
    assert answer["message"] == NO_PO_NL


EXPORT_TITLES = "po_number,line_id,item_code,description,quantity,unit,unit_price,price_basis,delivery_date"
# The made-up articles of the orders of these tests, by item code: the description, the quantity, the unit as an export prints it and
# the price. No order of the made-up set holds them, so that only the orders a test files can be the order of its cart.
ARTICLES = {"KM-9001": ("Slangklem 16-27 mm", "10", "st", "2.50"), "KM-9002": ("Kogelkraan DN15", "4", "st", "40.00"),
            "KM-9003": ("O-ring 30 x 3 mm", "100", "st", "0.20"), "KM-9004": ("Manometer 0-10 bar", "2", "st", "18.00"),
            "KM-9005": ("Knelkoppeling 15 mm", "6", "st", "7.50"), "KM-9006": ("Afdichtring 22 mm", "12", "st", "3.20"),
            "KM-9007": ("Drukregelaar 0-16 bar", "1", "st", "99.00")}
OWN = ("KM-9001", "KM-9002")                         # the items of the cart of most of these tests
NINE = datetime(2026, 11, 12, 9, 0)                  # the clock of deps(): the time of a capture, whose calendar day is today
EIGHT = datetime(2026, 11, 12, 8, 0).timestamp()     # the hour before the clock's: an order filed then was filed today
DAY = 24 * 3600                                      # EIGHT - DAY: filed yesterday
NOT_SURE_NL = ("De assistent weet niet zeker welke inkooporder bij deze winkelwagen hoort. Vergeleken met inkooporder {po}: kijk na of "
               "dit de goede is. Is deze winkelwagen voor een nieuwe inkooporder, geef dan eerst de export van de orderregels uit Exact "
               "(tabblad Bestanden) en herlaad deze pagina na een minuut. De assistent heeft niets voorbereid.")
NOT_SURE_EN = ("The assistant is not sure which purchase order this cart belongs to. Compared with purchase order {po}: check that it is "
               "the right one. If this cart is for a new purchase order, first give the export of the order lines from Exact (tab Files) "
               "and reload this page after a minute. The assistant has prepared nothing.")
SENT_NL = ("De inkooporder die het best bij deze winkelwagen past ({po}) is al verstuurd. Is dit een nieuwe bestelling, geef dan eerst de "
           "export van de orderregels uit Exact (tabblad Bestanden) en herlaad deze pagina na een minuut. De assistent heeft niets "
           "voorbereid.")
SENT_EN = ("The purchase order that fits this cart best ({po}) was sent already. If this is a new order, first give the export of the "
           "order lines from Exact (tab Files) and reload this page after a minute. The assistant has prepared nothing.")
NO_PO_NL = ("Geen inkooporder gevonden voor deze winkelwagen. Geef de assistent eerst de export van de orderregels uit Exact (tabblad "
            "Bestanden) en herlaad deze pagina na een minuut.")
NO_PO_EN = ("No purchase order found for this cart. First give the assistant the export of the order lines from Exact (tab Files) and "
            "reload this page after a minute.")
CHOSEN_NL, CHOSEN_EN = " (gekozen op de artikelen in de winkelwagen)", " (chosen by the items in the cart)"
CART_GUESS_NL = "Kijk na of dit de goede inkooporder is: de assistent koos haar op de artikelen in de winkelwagen."
CART_GUESS_EN = "Check that this is the right purchase order: the assistant chose it by the items in the cart."


def exported(r: Running, folder: Path, name: str, orders: dict[str, tuple], suppliers: dict[str, str] | None = None,
             at: float = EIGHT) -> None:
    """File a made-up export of the orders, {PO number: its item codes}, in the server's store, each as a look at its order, filed
    at the time `at`: by default an hour before the clock's, so today. With `suppliers` the export has a supplier column, which
    names the supplier of each order in it. An order whose rows the store holds already files nothing and keeps its time."""
    rows = [EXPORT_TITLES + (",supplier" if suppliers is not None else "")]
    for number, codes in orders.items():
        for n, code in enumerate(codes, 1):
            description, quantity, unit, price = ARTICLES[code]
            rows.append(f"{number},{n},{code},{description},{quantity},{unit},{price},1,2026-11-20"
                        + (f",{suppliers.get(number, '')}" if suppliers is not None else ""))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("\n".join(rows) + "\n", encoding="utf-8")
    new: list[str] = []
    r.deps.store.from_export(folder / name, {}, filed=new)
    for number in new:
        look = max((r.deps.store.root / number).glob("e*/po.json"), key=lambda path: int(path.parent.name[1:]))
        os.utime(look, (at, at))


def released(r: Running, folder: Path, number: str, codes: tuple, supplier: str = "S22", at: float = EIGHT) -> None:
    """File a made-up PO PDF of the order in the server's store, as a PDF version filed at the time `at` (by default today): an
    order that was sent."""
    spec = next(spec for spec in load_specs(REPO_ROOT / "aid" / "madeup" / "spec") if spec.case_id == "CART02")
    lines = [MadeUpPoLine(line_id=str(n), item_code=code, description=ARTICLES[code][0], quantity=ARTICLES[code][1], unit="pc",
                          unit_price=ARTICLES[code][3], price_basis="1", delivery_date="2026-11-20") for n, code in enumerate(codes, 1)]
    version = spec.po.versions[0].model_copy(update={"lines": lines})
    po = spec.po.model_copy(update={"po_number": number, "supplier": supplier, "versions": [version]})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{number}.pdf").write_bytes(pdf_bytes(po_pages(spec.model_copy(update={"po": po}), version), f"{number}.pdf"))
    stored = r.deps.store.add(folder / f"{number}.pdf")
    os.utime(r.deps.store.root / number / f"v{stored.version}" / "po.json", (at, at))


def filed(r: Running, number: str, seconds: int) -> None:
    """Set when the order was filed: every version and look of it `seconds` after eight o'clock of the clock's day."""
    for path in (r.deps.store.root / number).glob("*/po.json"):
        os.utime(path, (EIGHT + seconds, EIGHT + seconds))


def confirmed(r: Running, number: str, minute: int = 30, document: Path | None = None, found_by: str | None = None,
              reader=None, run: bool = True) -> Case:
    """A case of kind "confirmation" under the server's cases root that holds the order's number, opened at 8:`minute` for the
    document (by default the made-up confirmation MU01) and run with the server's Deps, or with `reader` in the reader's place:
    a supplier's confirmation of the order came in. `found_by` is how the number was guessed, when it was (case.json's
    po_found_by); with `run` false the case has no result yet."""
    case = open_case(r.settings.cases_root, "confirmation", [document or REPO_ROOT / "aid" / "madeup" / "out" / "MU01_conf.pdf"], number,
                     datetime(2026, 11, 12, 8, minute), found_by)
    if run:
        run_case(case, r.deps if reader is None else r.deps.model_copy(update={"reader": reader}))
    return case


def cart_page(codes: tuple = OWN, reference: str | None = None, supplier: str = "S22", prices: dict[str, str] | None = None,
              note: str = "") -> str:
    """A made-up cart page of the supplier with a line for each item code as ARTICLES gives it, the prices of `prices` in their
    place. It states the PO number `reference` when one is given; `note` is a part of the page that is no part of the cart."""
    rows = "".join(f"<tr><td>{code}</td><td>{ARTICLES[code][0]}</td><td>{ARTICLES[code][1]}</td><td>{ARTICLES[code][2]}</td>"
                   f"<td>€ {(prices or {}).get(code, ARTICLES[code][3].replace('.', ','))}</td><td>1</td></tr>" for code in codes)
    stated = f"<p>Uw referentie: {reference}</p>" if reference else ""
    return (f'<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Winkelwagen</title></head><body>{note}<h1>{supplier}</h1>'
            f"<p>Datum: 12-11-2026</p>{stated}<table><thead><tr><th>Artikelnummer</th><th>Omschrijving</th><th>Aantal</th>"
            f"<th>Eenheid</th><th>Prijs</th><th>Per</th></tr></thead><tbody>{rows}</tbody></table><p>Prijzen in EUR.</p></body></html>")


def read(tmp_path: Path, html: str):
    """The cart of a page as the cart reader reads it."""
    path = tmp_path / "read.html"
    path.write_text(html, encoding="utf-8")
    return read_cart_html(path)


def order_of(r: Running, tmp_path: Path, html: str, now: datetime = NINE) -> tuple[str | None, str | None]:
    """What cart_order gives for the cart of the page, on the server's store and cases, for a capture at `now`."""
    return cart_order(read(tmp_path, html), r.deps.store, r.settings.cases_root, now)


def capture_meta(r: Running, case_id: str) -> dict:
    return json.loads((r.settings.cases_root / case_id / "capture.json").read_text(encoding="utf-8"))


def result_of(r: Running, case_id: str) -> dict:
    return json.loads((r.settings.cases_root / case_id / "steps" / "08_result.json").read_text(encoding="utf-8"))


def close_records(r: Running, case_id: str) -> list[dict]:
    path = r.settings.cases_root / case_id / "steps" / "09_close.json"
    return json.loads(path.read_text(encoding="utf-8"))["records"] if path.is_file() else []


def open_cases(r: Running) -> list[str]:
    """The cases the review page offers: those in review."""
    return [summary.case_id for summary in list_open_cases(r.settings.cases_root)]


def prepared_rows(r: Running) -> list[str]:
    """The rows of the list of changes to enter in Exact that the server's writer prepared, without their title row."""
    path = r.deps.writer.path
    return path.read_text(encoding="utf-8").splitlines()[1:] if path.is_file() else []


def test_the_number_the_page_states_wins(capture_server, tmp_path):
    """Q1 (a): the PO of a cart is the number the page states when the store knows that PO, whatever other orders hold the cart's
    items and whatever items it shares with the cart: here an order that was sent beside two of today that hold exactly the cart's
    items. It is found, not guessed: the case has no po_found_by, the summary names the number alone, and the rules settle a small
    price difference as for any cart. So it is for an order that shares fewer than two items with the cart, and for a cart of one
    line. A number the store does not know is no order, and the items of the cart decide."""
    r = capture_server
    released(r, tmp_path, "250910", OWN)
    exported(r, tmp_path, "open.csv", {"250911": OWN, "250912": OWN})
    cart = read(tmp_path, cart_page(reference="250910"))
    assert cart.po_reference.value == "250910" and find_po(cart, r.deps.store, r.settings.cases_root, NINE) == "250910"
    assert order_of(r, tmp_path, cart_page(reference="250910")) == ("250910", None)
    status, answer = post(r, cart_page(reference="250910", prices={"KM-9001": "2,52"}))
    assert status == 200 and (answer["po_number"], answer["verdict"], answer["status"]) == ("250910", "match", "closed")
    assert answer["message"] == "Winkelwagen komt overeen met inkooporder 250910; 1 regel op de inkooporder voorbereid"
    assert load_case(r.settings.cases_root, answer["case_id"]).po_found_by is None
    assert set(capture_meta(r, answer["case_id"])) == {"sha256", "cart_digest", "lines_digest", "host", "page"}
    assert [action["rule_id"] for action in result_of(r, answer["case_id"])["actions"]] == ["R04"]
    # an order of one line, filed on an earlier day: found by its number for a cart of that line, and for a cart that shares one item
    exported(r, tmp_path, "one.csv", {"250913": ("KM-9003",)}, at=EIGHT - DAY)
    assert order_of(r, tmp_path, cart_page(("KM-9003",), reference="250913")) == ("250913", None)
    assert order_of(r, tmp_path, cart_page(("KM-9003", "KM-9004"), reference="250913")) == ("250913", None)
    assert order_of(r, tmp_path, cart_page(("KM-9005", "KM-9006"), reference="250913")) == ("250913", None)       # no item shared
    one = post(r, cart_page(("KM-9003",), reference="250913"))[1]
    assert (one["po_number"], one["verdict"], one["message"]) == ("250913", "match", "Winkelwagen komt overeen met inkooporder 250913")
    unknown = read(tmp_path, cart_page(reference="259999"))                    # a number the store does not know: the items decide
    assert unknown.po_reference.value == "259999"
    assert order_of(r, tmp_path, cart_page(reference="259999")) == ("250912", NOT_SURE) == order_of(r, tmp_path, cart_page())


def test_the_one_order_of_today_with_exactly_the_carts_items_is_found(tmp_path, madeup_out):
    """Q1 (b): the aid is sure by the items when exactly one stored order that was filed on the day of the capture holds exactly the
    cart's items and is not sent. The order is found: the case has no po_found_by, the rules apply (R04 prepares a small price
    difference), capture.json says how the order was found, and the summary says behind the number that it was chosen by the items,
    in the settings' language, for a match and for differences. One item code is enough: an order of one line is found for a cart
    of that one item."""
    for language, match, differ in (
            ("nl", f"Winkelwagen komt overeen met inkooporder 250931{CHOSEN_NL}; 1 regel op de inkooporder voorbereid",
             f"Inkooporder 250931{CHOSEN_NL}: 1 regel komt overeen en 1 verschil"),
            ("en", f"Cart matches purchase order 250931{CHOSEN_EN}; 1 line prepared on the purchase order",
             f"Purchase order 250931{CHOSEN_EN}: 1 line matches and 1 difference")):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            exported(r, tmp_path / language, "order.csv", {"250931": OWN})
            assert order_of(r, tmp_path, cart_page()) == ("250931", BY_ITEMS) and BY_ITEMS == "cart items"
            status, answer = post(r, cart_page(prices={"KM-9001": "2,52"}))     # 0.8 percent: within the thresholds
            assert status == 200 and (answer["po_number"], answer["verdict"], answer["status"]) == ("250931", "match", "closed")
            assert answer["message"] == match
            assert load_case(r.settings.cases_root, answer["case_id"]).po_found_by is None      # found, no guess: the rules apply
            assert capture_meta(r, answer["case_id"])["po_found_by"] == BY_ITEMS
            result = result_of(r, answer["case_id"])
            assert [action["rule_id"] for action in result["actions"]] == ["R04"]
            assert [(entry["rule_id"], entry["op"]["kind"], entry["status"]) for entry in result["write_entries"]] == [
                ("R04", "po_line_price", "prepared"), ("R04", "article_price", "prepared")]
            assert len(prepared_rows(r)) == 2 and all(",250931," in row for row in prepared_rows(r))
            above = post(r, cart_page(prices={"KM-9002": "44,00"}))[1]          # 10 percent: a difference for the buyer
            assert (above["po_number"], above["verdict"], above["message"]) == ("250931", "differences", differ)
            assert not [entry for entry in above["lines"] if entry["kind"] == "review"]
            exported(r, tmp_path / language, "line.csv", {"250932": ("KM-9003",)})                 # an order of one line
            assert order_of(r, tmp_path, cart_page(("KM-9003",))) == ("250932", BY_ITEMS)
            one = post(r, cart_page(("KM-9003",)))[1]
            assert (one["po_number"], one["verdict"], CHOSEN_NL in one["message"] or CHOSEN_EN in one["message"]) == ("250932", "match", True)


def test_an_order_of_an_earlier_day_is_a_guess(tmp_path, madeup_out):
    """Q1 (b) and (c): the same order, filed on the day before the capture, is no order the aid is sure of. The cart is compared
    with it as a guess of the kind "not sure": case.json holds how, so nothing is prepared and every difference is the buyer's (the
    pipeline's rule G01), the banner is amber and its first line says why, in the settings' language, and the review page names
    the guess in its own words. A cart of one line whose order was filed the day before is such a guess too, not a grey cart."""
    for language, sentence, label, message, line in (
            ("nl", NOT_SURE_NL, CART_GUESS_NL, "Inkooporder 250931: 1 regel komt overeen, 1 verschil en 1 reden voor de inkoper",
             "regel 1 (KM-9001): prijs 2.52 in de winkelwagen, 2.50 op de inkooporder (verschil)"),
            ("en", NOT_SURE_EN, CART_GUESS_EN, "Purchase order 250931: 1 line matches, 1 difference and 1 reason for the buyer",
             "line 1 (KM-9001): price 2.52 in the cart, 2.50 on the purchase order (different)")):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            exported(r, tmp_path / language, "order.csv", {"250931": OWN}, at=EIGHT - DAY)
            assert order_of(r, tmp_path, cart_page()) == ("250931", NOT_SURE) and NOT_SURE == "cart items, not sure"
            assert order_of(r, tmp_path, cart_page(), now=NINE - timedelta(days=1)) == ("250931", BY_ITEMS)      # on its own day: found
            status, answer = post(r, cart_page(prices={"KM-9001": "2,52"}))     # 0.8 percent: the rules would take it over (R04)
            assert status == 200 and (answer["po_number"], answer["verdict"], answer["status"]) == ("250931", "differences", "review")
            assert texts_of(answer) == [sentence.format(po="250931"), line]
            assert answer["lines"][0] == {"line_id": "", "item_code": None, "field": None, "kind": "review", "po_value": None,
                                          "doc_value": None, "reason": label, "text": sentence.format(po="250931")}
            assert answer["message"] == message
            case = load_case(r.settings.cases_root, answer["case_id"])
            assert case.po_found_by == NOT_SURE and capture_meta(r, answer["case_id"])["po_found_by"] == NOT_SURE
            result = result_of(r, answer["case_id"])
            assert [(action["rule_id"], action["route"], action["reason"]) for action in result["actions"]] == [
                ("G01", "REVIEW", GUESSED_CART)]
            assert result["write_entries"] == [] and [item["reason"] for item in result["review_items"]] == [GUESSED_CART]
            assert prepared_rows(r) == [] and open_cases(r) == [answer["case_id"]]            # nothing prepared
            same = post(r, cart_page())[1]                                      # a cart equal to the order is the buyer's to confirm too
            assert (same["verdict"], same["status"], texts_of(same)) == ("differences", "review", [sentence.format(po="250931")])
            exported(r, tmp_path / language, "line.csv", {"250932": ("KM-9003",)}, at=EIGHT - DAY)
            assert order_of(r, tmp_path, cart_page(("KM-9003",))) == ("250932", NOT_SURE)
            one = post(r, cart_page(("KM-9003",)))[1]
            assert (one["po_number"], one["verdict"], texts_of(one)) == ("250932", "differences", [sentence.format(po="250932")])
    assert (reason_label(GUESSED_CART, STRINGS["nl"]), reason_label(GUESSED_CART, STRINGS["en"])) == (CART_GUESS_NL, CART_GUESS_EN)
    assert GUESSED_CART == "PO chosen by the items in the cart" and GUESSED_CART != GUESSED_PO


def test_an_order_of_today_with_other_items_than_the_carts_is_a_guess(capture_server, tmp_path):
    """Q1 (b): the aid is sure only of an order that holds exactly the cart's items: the same set of item codes, and no line without
    an item code in the cart or on the order. An order of today with one item more than the cart, or one item less, is a guess; so
    is one with the cart's item codes beside a line that states none, and so is the order of a cart that has such a line."""
    r = capture_server
    exported(r, tmp_path, "more.csv", {"250921": (*OWN, "KM-9003")})
    assert order_of(r, tmp_path, cart_page()) == ("250921", NOT_SURE)                      # one item more on the order
    assert order_of(r, tmp_path, cart_page((*OWN, "KM-9003", "KM-9004"))) == ("250921", NOT_SURE)       # one item less on the order
    assert order_of(r, tmp_path, cart_page((*OWN, "KM-9003"))) == ("250921", BY_ITEMS)      # exactly the cart's items
    assert order_of(r, tmp_path, cart_page(("KM-9003", "KM-9002", "KM-9001"))) == ("250921", BY_ITEMS)   # in any order of the lines
    assert order_of(r, tmp_path, cart_page(("KM-9001", "KM-9001", "KM-9002", "KM-9003"))) == ("250921", BY_ITEMS)    # an item twice
    answer = post(r, cart_page(prices={"KM-9001": "2,52"}))[1]
    assert texts_of(answer) == [NOT_SURE_NL.format(po="250921"),
                                "regel 1 (KM-9001): prijs 2.52 in de winkelwagen, 2.50 op de inkooporder (verschil)",
                                "regel 3 (KM-9003): kon niet in de winkelwagen worden geplaatst"]
    # a line without an item code, in the cart: the cart's codes are the order's, yet the cart holds something more
    nameless = cart_page((*OWN, "KM-9003")).replace("</tbody>", "<tr><td></td><td>Zonder nummer</td><td>1</td><td>st</td><td>€ 1,00</td>"
                                                    "<td>1</td></tr></tbody>")
    cart = read(tmp_path, nameless)
    assert [line.item_code.value for line in cart.lines] == ["KM-9001", "KM-9002", "KM-9003", None]
    assert order_of(r, tmp_path, nameless) == ("250921", NOT_SURE)
    # and on the order: a look whose third line states no item code holds the cart's two codes and a line more
    exported(r, tmp_path, "pair.csv", {"250922": ("KM-9004", "KM-9005", "KM-9006")})
    look = r.deps.store.root / "250922" / "e1" / "po.json"
    held = json.loads(look.read_text(encoding="utf-8"))
    held["document"]["lines"][2]["item_code"] |= {"value": None, "text": None}
    look.write_text(json.dumps(held), encoding="utf-8")
    filed(r, "250922", 0)
    assert [line.item_code.value for line in r.deps.store.as_it_stands("250922").document.lines] == ["KM-9004", "KM-9005", None]
    assert order_of(r, tmp_path, cart_page(("KM-9004", "KM-9005"))) == ("250922", NOT_SURE)


def test_two_orders_of_today_with_exactly_the_carts_items_are_a_guess(capture_server, tmp_path):
    """Q1 (b) and (c): two orders filed today that both hold exactly the cart's items: the aid cannot tell which is the cart's, so
    the choice is a guess, compared with the one filed last, the later number among orders filed at the same time. So it stays when
    one of the two is sent: the cart is then compared with the other, still as a guess."""
    r = capture_server
    exported(r, tmp_path, "two.csv", {"250941": OWN, "250942": OWN})
    for seconds in (10, 20):                                                   # filed at the same time: the later number
        filed(r, "250941", seconds)
        filed(r, "250942", seconds)
        assert order_of(r, tmp_path, cart_page()) == ("250942", NOT_SURE)
    filed(r, "250941", 30)                                                     # the one filed last
    assert order_of(r, tmp_path, cart_page()) == ("250941", NOT_SURE)
    answer = post(r, cart_page(prices={"KM-9001": "2,52"}))[1]
    assert (answer["po_number"], texts_of(answer)[0], prepared_rows(r)) == ("250941", NOT_SURE_NL.format(po="250941"), [])
    released(r, tmp_path, "250941", OWN, at=EIGHT + 40)                         # 250941 is sent: the other one, and no order found for sure
    assert order_of(r, tmp_path, cart_page()) == ("250942", NOT_SURE)


def test_older_orders_do_not_disturb_the_one_order_of_today(capture_server, tmp_path):
    """Q1 (b): only the orders filed today that hold exactly the cart's items count for being sure. Older orders that are not sent
    and share two items with the cart, also one that holds exactly the cart's items, and an order of today with other items, leave
    the one order of today found."""
    r = capture_server
    exported(r, tmp_path, "old.csv", {"250951": OWN, "250952": (*OWN, "KM-9003"), "250953": ("KM-9001", "KM-9002", "KM-9004")},
             at=EIGHT - 30 * DAY)
    exported(r, tmp_path, "other.csv", {"250954": (*OWN, "KM-9005")})          # of today, with one item more
    assert order_of(r, tmp_path, cart_page()) == ("250954", NOT_SURE)           # no order of today holds exactly the cart's items yet
    exported(r, tmp_path, "new.csv", {"250955": OWN}, at=EIGHT + 60)
    assert order_of(r, tmp_path, cart_page()) == ("250955", BY_ITEMS)
    answer = post(r, cart_page(prices={"KM-9001": "2,52"}))[1]
    assert (answer["po_number"], answer["verdict"]) == ("250955", "match") and CHOSEN_NL in answer["message"]
    assert len(prepared_rows(r)) == 2 and all(",250955," in row for row in prepared_rows(r))


def test_an_order_of_today_that_is_sent_is_a_guess(tmp_path, madeup_out):
    """Q1, the word "sent", and (c): an order is sent when the store holds a PDF version of it, whatever a later export shows, or
    when a supplier's confirmation of it came in. The one order of today with exactly the cart's items is then no order the aid is
    sure of: the cart is compared with it as a guess of the kind "already sent", nothing is prepared, and the banner's first line
    says what to do when this is a new order, in the settings' language."""
    for language, sentence, message in (("nl", SENT_NL, "Inkooporder 250961: 1 regel komt overeen, 1 verschil en 1 reden voor de inkoper"),
                                        ("en", SENT_EN, "Purchase order 250961: 1 line matches, 1 difference and 1 reason for the buyer")):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            released(r, tmp_path / language, "250961", OWN)                     # sent by its PDF version
            assert order_of(r, tmp_path, cart_page()) == ("250961", ALREADY_SENT) and ALREADY_SENT == "cart items of an order already sent"
            status, answer = post(r, cart_page(prices={"KM-9001": "2,52"}))
            assert status == 200 and (answer["po_number"], answer["verdict"], answer["status"]) == ("250961", "differences", "review")
            assert texts_of(answer)[0] == sentence.format(po="250961") and len(texts_of(answer)) == 2
            assert answer["lines"][0]["reason"] == (CART_GUESS_NL if language == "nl" else CART_GUESS_EN)
            assert answer["message"] == message
            case = load_case(r.settings.cases_root, answer["case_id"])
            assert case.po_found_by == ALREADY_SENT and capture_meta(r, answer["case_id"])["po_found_by"] == ALREADY_SENT
            result = result_of(r, answer["case_id"])
            assert [action["rule_id"] for action in result["actions"]] == ["G01"] and result["write_entries"] == []
            assert prepared_rows(r) == []
            exported(r, tmp_path / language, "again.csv", {"250961": (*OWN, "KM-9003")}, at=EIGHT + 60)     # a later export of it
            assert r.deps.store.as_it_stands("250961").pdf_path.suffix == ".csv" and len(r.deps.store.versions("250961")) == 1
            assert order_of(r, tmp_path, cart_page((*OWN, "KM-9003"))) == ("250961", ALREADY_SENT)          # still sent
            # sent by a supplier's confirmation of it: an order known from its export alone
            other = ("KM-9004", "KM-9005")
            exported(r, tmp_path / language, "other.csv", {"250962": other})
            assert order_of(r, tmp_path, cart_page(other)) == ("250962", BY_ITEMS)
            confirmed(r, "250962")
            assert order_of(r, tmp_path, cart_page(other)) == ("250962", ALREADY_SENT)
            by_case = post(r, cart_page(other, prices={"KM-9004": "18,10"}))[1]
            assert (by_case["po_number"], texts_of(by_case)[0], prepared_rows(r)) == ("250962", sentence.format(po="250962"), [])


def test_only_a_suppliers_own_confirmation_makes_an_order_sent(capture_server, tmp_path):
    """Q1, the word "sent": a case counts as a supplier's confirmation of an order when it is no cart's and no try-out's, holds the
    order's number, found by the number recognition (no po_found_by in its case.json), and is not failed. A cart's case of the
    order, a try-out confirmation of it, a confirmation whose number was a guess, a confirmation whose run failed and a case of
    another order leave the order not sent: it is still found for sure. A confirmation that has no result yet is not known to have
    failed, and counts."""
    r = capture_server
    carts = {"250971": ("KM-9001", "KM-9002"), "250972": ("KM-9003", "KM-9004"), "250973": ("KM-9001", "KM-9003"),
             "250974": ("KM-9002", "KM-9004"), "250975": ("KM-9001", "KM-9004"), "250976": ("KM-9002", "KM-9003"),
             "250977": ("KM-9005", "KM-9006")}
    exported(r, tmp_path, "orders.csv", carts)
    for number, codes in carts.items():
        assert order_of(r, tmp_path, cart_page(codes)) == (number, BY_ITEMS), number

    def failing(path, kind):
        raise ReaderError("made-up reader failure")
    own = post(r, cart_page(carts["250971"]))[1]                                # a cart's own case of the order
    assert own["case_id"].startswith("250971_") and load_case(r.settings.cases_root, own["case_id"]).kind == "cart"
    tryout = tmp_path / "PROEF 250972.pdf"
    tryout.write_bytes(confirmation_pdf(r.deps.store.as_it_stands("250972"), "none", NINE))
    assert tryout_case(confirmed(r, "250972", document=tryout))                 # a try-out confirmation of the order
    assert confirmed(r, "250973", found_by=FOUND_BY_LINES).po_found_by == FOUND_BY_LINES       # a confirmation whose number was a guess
    failed = confirmed(r, "250974", reader=failing)                             # a confirmation whose run failed
    assert result_of(r, failed.case_id)["status"] == "failed"
    confirmed(r, "250975", run=False)                                           # a confirmation without a result yet
    real = confirmed(r, "250977")                                               # a supplier's confirmation that was run
    assert result_of(r, real.case_id)["status"] != "failed" and real.po_found_by is None
    for number, how in (("250971", BY_ITEMS), ("250972", BY_ITEMS), ("250973", BY_ITEMS), ("250974", BY_ITEMS),
                        ("250975", ALREADY_SENT), ("250976", BY_ITEMS), ("250977", ALREADY_SENT)):
        assert order_of(r, tmp_path, cart_page(carts[number])) == (number, how), number
    confirmed(r, "250974", minute=40)                                           # a second confirmation of 250974, which did not fail
    assert order_of(r, tmp_path, cart_page(carts["250974"])) == ("250974", ALREADY_SENT)
    (r.settings.cases_root / "250976_notes").mkdir()                            # a folder that names the order and is no case
    (r.settings.cases_root / "250976_20261112T083000").mkdir()
    assert order_of(r, tmp_path, cart_page(carts["250976"])) == ("250976", BY_ITEMS)


def test_the_order_a_guessed_cart_is_compared_with(capture_server, tmp_path):
    """Q1 (c): the candidates of a guess are the orders that share at least two item codes with the cart and those that hold
    exactly the cart's items. The cart is compared with one of them, chosen in this order: those filed today, when there are any;
    then those that hold exactly the cart's items; then those that are not sent; then those of the page's supplier; then the one
    filed last. The guess is "already sent" when that order is sent, else "not sure"."""
    r = capture_server
    long_ago = EIGHT - 30 * DAY
    # only sent orders, of earlier days: the one with exactly the cart's items, although the other was filed later
    released(r, tmp_path, "250980", OWN, at=long_ago)
    released(r, tmp_path, "250981", (*OWN, "KM-9003"), at=long_ago + 60)
    assert order_of(r, tmp_path, cart_page()) == ("250980", ALREADY_SENT)
    # exactly the cart's items before not sent: an old order that is not sent and shares two items does not take its place
    exported(r, tmp_path, "old.csv", {"250982": (*OWN, "KM-9004")}, at=long_ago + 120)
    assert order_of(r, tmp_path, cart_page()) == ("250980", ALREADY_SENT)
    # filed today before exactly the cart's items: a new order of today with one item more than the cart is the order compared with
    exported(r, tmp_path, "new.csv", {"250983": (*OWN, "KM-9005")})
    assert order_of(r, tmp_path, cart_page()) == ("250983", NOT_SURE)
    answer = post(r, cart_page(prices={"KM-9001": "2,52"}))[1]
    assert (answer["po_number"], answer["verdict"]) == ("250983", "differences") and prepared_rows(r) == []
    assert texts_of(answer) == [NOT_SURE_NL.format(po="250983"),
                                "regel 1 (KM-9001): prijs 2.52 in de winkelwagen, 2.50 op de inkooporder (verschil)",
                                "regel 3 (KM-9005): kon niet in de winkelwagen worden geplaatst"]      # the line the cart lacks shows
    # the cart's own order released today, with exactly the cart's items, beside the old order that is not sent: today's order
    other = ("KM-9003", "KM-9004")
    exported(r, tmp_path, "older.csv", {"250984": (*other, "KM-9006")}, at=long_ago)
    released(r, tmp_path, "250985", other)
    assert order_of(r, tmp_path, cart_page(other)) == ("250985", ALREADY_SENT)
    sent = post(r, cart_page(other))[1]
    assert (sent["po_number"], texts_of(sent)) == ("250985", [SENT_NL.format(po="250985")])


def test_a_guess_prefers_the_order_not_sent_then_the_pages_supplier_then_the_one_filed_last(capture_server, tmp_path):
    """Q1 (c), the last three steps of the choice, on orders of an earlier day. Of two orders with exactly the cart's items the one
    that is not sent is compared with, although the other is of the page's supplier. Of orders that are not sent the one of the
    page's supplier, also when no order holds exactly the cart's items and another was filed later. A page that names no supplier
    prefers no order for its supplier: the one filed last. The supplier never makes the aid sure."""
    r = capture_server
    old = EIGHT - 3 * DAY
    released(r, tmp_path, "250990", OWN, supplier="S22", at=old + 300)          # sent, of the page's supplier, filed last
    exported(r, tmp_path, "open.csv", {"250991": OWN}, suppliers={"250991": "S21"}, at=old)
    assert order_of(r, tmp_path, cart_page(supplier="S22")) == ("250991", NOT_SURE)
    # no order holds exactly the cart's items: the page's supplier decides before the time of filing
    items = ("KM-9003", "KM-9004")
    exported(r, tmp_path, "named.csv", {"250992": (*items, "KM-9005"), "250993": (*items, "KM-9006")},
             suppliers={"250992": "S22", "250993": "S21"}, at=old)
    filed(r, "250993", -3 * DAY + 60)                                           # the order of the other supplier was filed later
    assert order_of(r, tmp_path, cart_page(items, supplier="S22")) == ("250992", NOT_SURE)
    assert order_of(r, tmp_path, cart_page(items, supplier="S21")) == ("250993", NOT_SURE)
    assert order_of(r, tmp_path, cart_page(items, supplier="S20")) == ("250993", NOT_SURE)       # no order of that supplier: filed last
    # a page that names no supplier, beside an order that names none: no preference, the one filed last
    exported(r, tmp_path, "nameless.csv", {"250994": (*items, "KM-9007")}, at=old - 60)
    assert r.deps.store.as_it_stands("250994").supplier is None
    bare = cart_page(items).replace("<title>Winkelwagen</title>", "").replace("<h1>S22</h1>", "")
    assert read(tmp_path, bare).supplier.value is None
    assert order_of(r, tmp_path, bare) == ("250993", NOT_SURE)
    # the supplier is no evidence for being sure: the one order of today with exactly the cart's items is found, whoever it names
    exported(r, tmp_path, "today.csv", {"250995": ("KM-9005", "KM-9006")}, suppliers={"250995": "S21"})
    assert order_of(r, tmp_path, cart_page(("KM-9005", "KM-9006"), supplier="S22")) == ("250995", BY_ITEMS)


def test_today_is_the_calendar_day_of_the_clock_of_the_capture(capture_server, tmp_path):
    """Q1, the words "filed today": the order was filed on the calendar day of the capture, by the clock of the capture. For a clock
    that names its zone that is the day in that zone: an order filed at eight in the morning, local time, was filed today for a
    capture at nine and at midnight's edge of that local day, and on the day before for a clock nine hours ahead that reads the
    next day already."""
    r = capture_server
    exported(r, tmp_path, "order.csv", {"250931": OWN})
    html = cart_page()
    for now, how in ((NINE, BY_ITEMS), (NINE.replace(hour=23, minute=59, second=59), BY_ITEMS), (NINE.replace(hour=0, minute=0), BY_ITEMS),
                     (NINE + timedelta(days=1), NOT_SURE), (NINE.replace(hour=0, minute=0) + timedelta(days=1), NOT_SURE),
                     (NINE - timedelta(days=1), NOT_SURE)):
        assert order_of(r, tmp_path, html, now=now) == ("250931", how), now
    local = NINE.replace(hour=16).astimezone()                                  # four in the afternoon, with the local zone named
    ahead = timezone(local.utcoffset() + timedelta(hours=9))
    assert order_of(r, tmp_path, html, now=local) == ("250931", BY_ITEMS)
    assert local.astimezone(ahead).date() == date(2026, 11, 13)
    assert order_of(r, tmp_path, html, now=local.astimezone(ahead)) == ("250931", NOT_SURE)
    assert order_of(r, tmp_path, html, now=NINE.astimezone().astimezone(ahead)) == ("250931", BY_ITEMS)       # 18:00 there, the same day
    filed(r, "250931", -12 * 3600)                      # filed at eight in the evening of the day before, local time
    night = NINE.replace(hour=1)                        # one at night: the next local day, and the same day nine hours ahead
    assert order_of(r, tmp_path, html, now=night) == ("250931", NOT_SURE)
    assert order_of(r, tmp_path, html, now=night.astimezone().astimezone(ahead)) == ("250931", BY_ITEMS)


def test_a_number_the_store_does_not_know_decides_nothing(capture_server, tmp_path):
    """Q1 and the review's finding 2: the buyer typed the number of the new order in the cart's reference field and forgot its
    export. The store does not know that number, so the items decide: beside an order of an earlier day with two of the cart's
    items the cart is a guess, nothing is prepared on that other order, and once the export of the stated order is filed, the
    reload finds it by its number: a new case is opened for it, and the guess is finished as replaced."""
    r = capture_server
    exported(r, tmp_path, "earlier.csv", {"260001": ("KM-9001", "KM-9002", "KM-9003")}, at=EIGHT - 3 * DAY)
    html = cart_page(("KM-9001", "KM-9002", "KM-9004"), reference="260002", prices={"KM-9001": "2,52"})
    assert read(tmp_path, html).po_reference.value == "260002" and r.deps.store.as_it_stands("260002") is None
    assert order_of(r, tmp_path, html) == ("260001", NOT_SURE)
    guess = post(r, html)[1]
    assert (guess["po_number"], guess["verdict"], guess["status"]) == ("260001", "differences", "review")
    assert texts_of(guess)[0] == NOT_SURE_NL.format(po="260001") and CHOSEN_NL not in guess["message"]
    assert [action["rule_id"] for action in result_of(r, guess["case_id"])["actions"]] == ["G01"] and prepared_rows(r) == []
    exported(r, tmp_path, "stated.csv", {"260002": ("KM-9001", "KM-9002", "KM-9004")})
    assert order_of(r, tmp_path, html) == ("260002", None)
    found = post(r, html)[1]
    assert (found["po_number"], found["case_id"], found["verdict"]) == ("260002", "260002_20261112T090001", "match")
    assert found["message"] == "Winkelwagen komt overeen met inkooporder 260002; 1 regel op de inkooporder voorbereid"
    assert capture_meta(r, guess["case_id"])["replaced_by"] == found["case_id"] and result_of(r, guess["case_id"])["status"] == "closed"
    assert open_cases(r) == [] and all(",260002," in row for row in prepared_rows(r)) and len(prepared_rows(r)) == 2
    assert post(r, html.replace("<h1>", "<!-- reloaded --><h1>"))[1] == found and len(case_folders(r)) == 2


def test_a_stated_number_that_goes_on_is_not_read_and_the_items_decide(capture_server, tmp_path):
    """Q22: a number on the page that goes on behind an underscore, a comma, a colon or an en dash with a digit is another number
    than the order's, so the reader reads none and the items decide: the cart is checked against the one order of today with
    exactly its items, never against the order whose number the page's number begins with. The same number with such a sign at
    its end, or before a space, is the order's number."""
    r = capture_server
    exported(r, tmp_path, "own.csv", {"260001": OWN})
    exported(r, tmp_path, "other.csv", {"260002": ("KM-9003", "KM-9004")})
    for stated in ("260002_01", "260002,5", "260002:2", "260002\u20132"):
        html = cart_page(reference=stated)
        assert read(tmp_path, html).po_reference.value is None, stated
        assert order_of(r, tmp_path, html) == ("260001", BY_ITEMS), stated
    for stated in ("260002.", "260002,", "260002:", "260002, met dank", "260002: zie hieronder"):
        html = cart_page(reference=stated)
        assert read(tmp_path, html).po_reference.value == "260002", stated
        assert order_of(r, tmp_path, html) == ("260002", None), stated
    answer = post(r, cart_page(reference="260002_01"))[1]
    assert (answer["po_number"], answer["verdict"]) == ("260001", "match") and CHOSEN_NL in answer["message"]


def test_the_reviews_states_of_doubt_are_guesses_now(tmp_path, madeup_out):
    """The review's scenarios b, c, d and d2 (its findings 3, 4 and 5), with what they give under ruling Q1. Each was an order
    taken as found, with a change prepared on it, although the cart may be another order's."""
    clock = [NINE]
    with running(tmp_path / "b", madeup_out) as r:      # b: an order known only from an export of two months ago, never sent
        three = ("KM-9001", "KM-9002", "KM-9003")
        exported(r, tmp_path / "b", "old.csv", {"260001": three}, at=EIGHT - 60 * DAY)
        answer = post(r, cart_page(three, prices={"KM-9001": "2,52"}))[1]       # today's cart with those items, for a new order
        assert (answer["po_number"], answer["verdict"], answer["status"]) == ("260001", "differences", "review")
        assert texts_of(answer)[0] == NOT_SURE_NL.format(po="260001") and prepared_rows(r) == [] and open_cases(r) == [answer["case_id"]]
    with running(tmp_path / "c", madeup_out) as r:      # c: the one candidate is of another supplier than the page's, and holds more
        exported(r, tmp_path / "c", "named.csv", {"260001": ("KM-9001", "KM-9002", "KM-9003")}, suppliers={"260001": "S21"})
        answer = post(r, cart_page(supplier="S22", prices={"KM-9001": "2,52"}))[1]
        assert (answer["po_number"], answer["verdict"]) == ("260001", "differences") and prepared_rows(r) == []
        assert texts_of(answer) == [NOT_SURE_NL.format(po="260001"),
                                    "regel 1 (KM-9001): prijs 2.52 in de winkelwagen, 2.50 op de inkooporder (verschil)",
                                    "regel 3 (KM-9003): kon niet in de winkelwagen worden geplaatst"]
    four = ("KM-9001", "KM-9002", "KM-9003", "KM-9004")
    with running(tmp_path / "d", madeup_out) as r:      # d: the cart's own order beside another of the same export, then released
        exported(r, tmp_path / "d", "two.csv", {"260007": four, "260010": ("KM-9001", "KM-9002", "KM-9007")})
        html = cart_page(four, prices={"KM-9001": "2,52"})
        first = post(r, html)[1]                        # the one order of today with exactly the cart's items: found
        assert (first["po_number"], first["verdict"]) == ("260007", "match") and CHOSEN_NL in first["message"]
        released(r, tmp_path / "d", "260007", four, at=EIGHT + 600)
        assert order_of(r, tmp_path, html) == ("260007", ALREADY_SENT)          # never the other order, 260010
        assert post(r, html)[1] == first and case_folders(r) == [first["case_id"]]          # and its found case keeps answering
        assert all(",260007," in row for row in prepared_rows(r)) and len(prepared_rows(r)) == 2
    with running(tmp_path / "d2", madeup_out) as r:     # d2: the same cart opened again the next day, in a page of other bytes
        r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
        exported(r, tmp_path / "d2", "own.csv", {"260007": four})
        first = post(r, cart_page(four, prices={"KM-9001": "2,52"}))[1]
        assert (first["po_number"], first["verdict"]) == ("260007", "match") and len(prepared_rows(r)) == 2
        released(r, tmp_path / "d2", "260007", four, at=EIGHT + 600)
        exported(r, tmp_path / "d2", "next.csv", {"260010": ("KM-9001", "KM-9002", "KM-9007")}, at=EIGHT + 1200)
        clock[0] = NINE + timedelta(days=1)
        second = post(r, cart_page(four, prices={"KM-9001": "2,52"}, note="<!-- next day -->"))[1]
        assert (second["po_number"], second["case_id"]) == ("260007", "260007_20261113T090000")
        assert texts_of(second) == [SENT_NL.format(po="260007"),
                                    "regel 1 (KM-9001): prijs 2.52 in de winkelwagen, 2.50 op de inkooporder (verschil)"]
        assert len(prepared_rows(r)) == 2 and all(",260007," in row for row in prepared_rows(r))      # nothing for 260010


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
                 "/testshop/CART02_export.csv", "/testshop/MISSING.html", "/testshop/", "/testshop/.html",
                 "/testshop/" + "a" * 295 + ".html"):                           # a name the file system refuses
        assert raw_request(capture_server, "GET", path, {})[0] == 404, path[:40]


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


class _Outbox(FileMailer):
    """A mailer that sends nothing and keeps each message it was given."""

    def __init__(self, folder: Path):
        super().__init__(folder, clock=lambda: NINE)
        self.given: list = []

    def send(self, message):
        self.given.append(message)
        return super().send(message)


@pytest.mark.parametrize("language, reason", [
    ("nl", "Inkooporder PO 250201 wordt gewijzigd, omdat de winkelwagen bij leverancier {supplier} afwijkt van de order:"),
    ("en", "Purchase order PO 250201 is being changed, because the cart at supplier {supplier} differs from the order:")])
def test_the_request_to_the_approver_of_a_carts_case_names_the_cart(tmp_path, madeup_out, language, reason):
    """Ruling Q19: an order above the authorization that the pre-check changes for a cart sends the approver the request to approve
    it again. No order confirmation exists yet, so its reason names the cart at the supplier, from the run (the rule R10) and from
    a close on the review page, in the settings' language; the supplier is the case's."""
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"authorization_limit": Decimal("50"), "language": language}))
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store"})
    batch.file_po_dir(s, madeup_out)
    outbox = _Outbox(tmp_path / "outbox")
    d = d.model_copy(update={"settings": s, "mailer": outbox})
    answer = precheck(page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15", "€ 6,20": "€ 7,00"}), SHOP, d, s)
    assert (answer.po_number, answer.status) == ("250201", "review")
    case = load_case(s.cases_root, answer.case_id)
    result = json.loads((case.root / "steps" / "08_result.json").read_text(encoding="utf-8"))
    said = reason.format(supplier=result["supplier"])
    assert result["supplier"] and [message.template for message in outbox.given] == ["reapproval"]         # the run's R10
    assert said in " ".join(outbox.given[0].body.split()) and outbox.given[0].to == [s.approver_email]
    for word in ("orderbevestiging", "order confirmation"):
        assert word not in outbox.given[0].body
    for item in result["review_items"]:
        decide(case, item["item_id"], "confirm")
    closed = close_case(case, d)
    assert closed.status == "closed" and [message.template for message in outbox.given] == ["reapproval", "reapproval"]   # the close
    assert said in " ".join(outbox.given[1].body.split()) and "7.00" in outbox.given[1].body
    for word in ("orderbevestiging", "order confirmation"):
        assert word not in outbox.given[1].body


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
    def broken(cart, store, cases_root, now):
        raise RuntimeError("made-up store failure")
    monkeypatch.setattr(server_module, "cart_order", broken)
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
    began. The pass scans a watched folder under tmp_path, so that it writes nothing into the repository (Task 37, amendment 10: the
    example settings' "inbox" lies beside aid/settings.example.json)."""
    d = deps(tmp_path, madeup_out)
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox"})
    batch.file_po_dir(s, madeup_out)
    start = datetime(2026, 11, 12, 9, 0)
    late = start + runner.RUNNING_FRESH + timedelta(minutes=5)          # the server's start, long after the pass began
    times = iter([start])
    d = d.model_copy(update={"settings": s, "clock": lambda: next(times, late + timedelta(seconds=10))})
    case = server_module.open_capture_case(s.cases_root, madeup_out / "CART02.html", "250202", late)
    (case.root / runner.RUNNING_FILE).write_text(json.dumps({"pid": 1, "started": late.isoformat()}), encoding="utf-8")
    runner._pass(runner._Loop(d))                                  # one pass of a loop over these Deps
    assert not (case.root / "steps" / "08_result.json").exists()
    assert (tmp_path / "inbox").is_dir() and not (REPO_ROOT / "aid" / "inbox").exists()


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
    """The runner reports a case of the capture server in its digest once. Its passes scan a watched folder under tmp_path, so that
    the test writes nothing into the repository (Task 37, amendment 10)."""
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"digest": "per_case"}))
    s = d.settings.model_copy(update={"po_store_root": tmp_path / "store", "watched_folder": tmp_path / "inbox"})
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
    assert (tmp_path / "inbox").is_dir() and not (REPO_ROOT / "aid" / "inbox").exists()


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


ROUTE_SENTENCE = ('exact_base_url is set, but this version of the aid writes nothing in Exact: every change is prepared for the tab '
                  '"In Exact invoeren". Remove exact_base_url from the settings file.')


def test_main_refuses_a_settings_file_in_which_exact_base_url_is_set(tmp_path, capsys, monkeypatch):
    """Ruling V10: no command builds the write route into Exact. The capture server ends with 2 and the one sentence before it
    listens or writes anything."""
    monkeypatch.setattr(server_module, "serve", lambda *a, **k: pytest.fail("the server was started"))
    path = _settings_file(tmp_path, capture_token=TOKEN, exact_base_url="https://exact.example.com/api")
    assert main(["--settings", str(path), "--port", "0"]) == 2
    err = capsys.readouterr().err
    assert err.splitlines() == [f"aid.capture_server: {ROUTE_SENTENCE}"] and "exact.example.com" not in err
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["settings.json"]


def test_the_capture_servers_writer_is_the_prepared_writer_whatever_the_settings_hold(tmp_path):
    """Ruling V10: the Deps of the pre-check hold the prepared writer, also for settings that name a route (set in code, as no
    file can hold them), and with a token in the environment."""
    settings = load_settings(_settings_file(tmp_path, capture_token=TOKEN))
    for held in (settings, settings.model_copy(update={"exact_base_url": "https://exact.example.com/api"})):
        d = precheck_deps(held, {"AID_EXACT_TOKEN": "made-up-token"})
        assert type(d.writer) is PreparedWriter and d.writer.out_dir == tmp_path / "cases" / "prepared"


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
PRICE_ABOVE_TOO = {"€ 4,10": "€ 4,26"}       # another cart with a like difference: 3.9 percent, above 2 percent and within 5


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


def test_a_running_server_keeps_its_settings_when_the_file_gets_exact_base_url(tmp_path, madeup_out):
    """Ruling V10: a settings file that gets the key while the server runs is not used: the settings in use are kept, nothing is
    rebuilt, and capture.log holds the one sentence."""
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        data = json.loads(path.read_text(encoding="utf-8")) | {"exact_base_url": "https://exact.example.com/api"}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        touch_later(path, 1)
        status, answer = post(r, page(madeup_out, "CART01.html"))
        assert status == 200 and answer["verdict"] == "match" and rebuilt == [] and r.server.deps is r.deps
        assert r.server.settings.exact_base_url is None
        log = (r.settings.cases_root / "capture.log").read_text(encoding="utf-8")
        assert f"the changed settings file is not used: {ROUTE_SENTENCE}; the settings in use are kept" in log
        assert "exact.example.com" not in log


def test_raised_thresholds_in_the_settings_file_give_the_same_server_another_verdict(tmp_path, madeup_out):
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        status, first = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE))
        assert status == 200 and (first["verdict"], first["status"]) == ("differences", "review") and rebuilt == []
        data = json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "5"}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        touch_later(path, 1)
        second_page = page(madeup_out, "CART01.html", texts=PRICE_ABOVE_TOO)                # another cart, a like difference
        status, second = post(r, second_page)
        assert status == 200 and (second["verdict"], second["status"]) == ("match", "closed") and second["case_id"] != first["case_id"]
        assert r.server.settings.price_pct == Decimal("5") and r.server.deps.settings.price_pct == Decimal("5")
        # the server called its rebuild, once, and works with the Deps it made: the change of the second cart went to their writer
        assert len(rebuilt) == 1 and r.server.deps is rebuilt[0] and r.server.deps is not r.deps
        assert (tmp_path / "rebuilt" / "prepared_changes.csv").is_file() and not (tmp_path / "prepared_changes.csv").exists()
        assert post(r, second_page)[0] == 200 and len(rebuilt) == 1                          # no change of the file: no rebuild


def test_a_cart_checked_before_the_thresholds_changed_keeps_its_outcome_until_the_cart_itself_changes(tmp_path, madeup_out):
    """What the manual tells the buyer after a save (section 2.8): the same cart is answered from its case, a changed cart is
    checked anew with the new settings."""
    with watching(tmp_path, madeup_out) as (r, path, rebuilt):
        cart = page(madeup_out, "CART01.html", texts=PRICE_ABOVE)
        first = post(r, cart)[1]
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"price_pct": "5"}, indent=2), encoding="utf-8")
        touch_later(path, 1)
        again = post(r, cart)[1]
        assert (again["case_id"], again["verdict"]) == (first["case_id"], "differences") and r.server.settings.price_pct == Decimal("5")
        changed = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE_TOO))[1]
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
        assert post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE_TOO))[0] == 200
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
        assert post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE_TOO))[0] == 200
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
        status, answer = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE_TOO))
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
        status, again = post(r, page(madeup_out, "CART01.html", texts=PRICE_ABOVE_TOO))
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
    """The checkers' sequence (third review round, K19), as it runs since Task 43: CART01 at 4,15 on 12 November (case A, R04 4.10 ->
    4.15), ticked; a newer value for the line dropped; the same cart on 13 November (case C) is compared with the ticked 4.15: it
    matches through that entered value (its step 03_entered), asks nothing and has no row and no write of its own. A's change is
    put back. The pre-check judges C's match by the list as it is (W3, _rested): while the put-back waits it says "terugzetten
    staat op de lijst In Exact invoeren", and once the put-back is entered the line is a difference, "(wijziging ongedaan
    gemaakt)", and the cart is not green. K19's own rule, a prepared write judged by a put-back that another case's log holds, is
    tested by test_two_captures_before_the_tick_share_one_change_and_its_put_back."""
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
    assert states == ["entered", "dropped"]         # since Task 43 case C is compared with the ticked 4.15 and asks nothing again
    assert load_case(prepared.parent, second["case_id"]).read_step("entered")["replaced"][0]["entered"] == "4.15"
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
    """The checkers' sequence (third review round, K19), as it runs since Task 43: the same cart on 12 and 13 November, with no
    other value in between; the tick came before the second capture, so the second case matches the ticked price and the change
    holds the entry of the first case alone. The put-back undoes that entry, and the second case's pre-check, which judges its
    match by the list (W3), says that it waits, then, once it is entered, "(wijziging ongedaan gemaakt)"."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    [run] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    assert len(run.case_ids) == 1 and run.state == "entered"      # since Task 43 the second case matches the ticked 4.15: no row of its own
    assert second["verdict"] == "match" and second["case_id"] != first["case_id"]
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
    """The checkers' sequence (fourth review round, K29), as it runs since Task 43: on CART01's line 2, other cases ask 4.10 -> 4.30,
    4.30 -> 4.15 (X1), 4.15 -> 4.30 and 4.10 -> 4.15 (X3), all entered, and 4.10 -> 4.30, which is open, when the cart at 4,15 is
    captured: case C is compared with X3's ticked 4.15, matches through it and has no row of its own. X3's put-back is entered:
    C's match, judged by the list (W3), is amber with "(wijziging ongedaan gemaakt)". The put-back of X1, another change of the
    same value, waits and is dropped: C stays amber, as Exact holds 4.10 all along. K29's own rule, a replaced row judged by the
    put-back that took its value out of Exact, is tested by the list's model (test_hand_list_model.py)."""
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
    """The acceptance harness of the fourth review round (seed 7330 and its kind), as it runs since Task 43: on CART01's line 2
    another case's 4.10 -> 4.15 (X) is entered; a change to 4.30 is open when the cart at 4,15 is captured: case C is compared with
    X's ticked 4.15, matches through it, has no row of its own, and the open 4.30, which its cart no longer asks, leaves the list
    (W4). A newer document then asks 4.15 -> 4.10 and is dropped; X is put back, and its put-back entered: Exact holds 4.10. C's
    match, judged by the list (W3), is amber with "(wijziging ongedaan gemaakt)", and while the put-back waits, "terugzetten staat
    op de lijst In Exact invoeren"."""
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
    # since Task 43 case C is compared with X's ticked 4.15 and has no row of its own; the open 4.30, which C's cart no longer
    # asks, left the list with it
    assert states == [("4.15", "R04", "entered"), ("4.30", "R04", "replaced"), ("4.10", "R04", "dropped"), ("4.10", "undo", "open")]
    assert "terugzetten staat op de lijst In Exact invoeren" in _flag_of(post(capture_server, html)[1], "2")
    [put] = [change for change in changes(prepared)[0] if change.puts_back == x]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 11, 10, 5))
    undone = post(capture_server, html)[1]
    assert undone["verdict"] == "differences" and _flag_of(undone, "2") == UNDONE


# --- Task 43: a cart is compared with the order as Exact holds it by the list ---

def test_the_same_cart_after_the_tick_is_compared_with_the_entered_price(capture_server, madeup_out):
    """Rule 7 and amendment 6 at the pre-check: CART01 at 4,15, a price within the thresholds, which the rules prepare; the buyer
    enters it in Exact and ticks it. The same cart shown on a later day is checked against the same look at the order, filed
    before the tick, with the entered price laid over it: it matches, the bar shows 4.15 as the order's price, nothing is prepared
    again and the list gets no row. A cart at 4,30 is then shown against the entered price, not against the 4.10 of the export."""
    from aid.execution.by_hand import changes, mark
    from aid.tests.helpers import filed_on

    prepared = capture_server.settings.cases_root / "prepared"
    filed_on(capture_server.deps.store, "250201", "e1", datetime(2026, 11, 1, 9, 0))      # before the tick, whatever day the suite runs

    def shown(day: int, price: str) -> dict:
        """The answer for CART01 with line 2 at that price, shown on that day of November at 9:00."""
        capture_server.server.deps = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared),
                                                                            "clock": lambda: datetime(2026, 11, day, 9, 0)})
        html = page(madeup_out, "CART01.html", texts={"€ 4,10": f"€ {price}"}).replace("</body>", f"<!-- shown {day}-11 -->\n</body>")
        return post(capture_server, html)[1]

    first = shown(12, "4,15")
    assert (first["verdict"], first["message"]) == ("match", "Winkelwagen komt overeen met inkooporder 250201; 1 regel op de inkooporder voorbereid")
    [asked] = [change for change in changes(prepared)[0] if change.kind == "po_line_price"]
    assert mark(prepared, asked.ids, "entered", datetime(2026, 11, 12, 10, 0)) == 1
    second = shown(13, "4,15")
    assert second["case_id"] != first["case_id"]
    assert (second["verdict"], second["message"]) == ("match", "Winkelwagen komt overeen met inkooporder 250201")
    assert {entry["kind"] for entry in second["lines"]} == {"match"}
    price = next(entry for entry in second["lines"] if entry["field"] == "price" and entry["line_id"] == "2")
    assert (price["po_value"], price["doc_value"], price["text"]) == ("4.15", "4.15", "regel 2 (KM-2102): prijs 4.15")
    assert [change.ids for change in changes(prepared)[0] if change.kind == "po_line_price"] == [asked.ids]      # nothing asked again
    third = shown(14, "4,30")                                                    # 3.6 percent above the entered price
    assert third["verdict"] == "differences"
    assert _flag_of(third, "2") == "regel 2 (KM-2102): prijs 4.30 in de winkelwagen, 4.15 op de inkooporder (prijsverschil boven de drempel)"


def test_a_stored_match_through_an_entered_value_is_judged_by_the_list_as_it_is(capture_server, madeup_out):
    """The review round of Task 43, W3 (finding 2): CART01 at 4,15 on 12 November is prepared and ticked; on 13 November the same
    cart matches through the ticked price and has no row of its own. A later capture of that cart is answered by that case only
    after its entered value is checked against the list as it is now: once the buyer takes the tick back, or another value is
    entered for the line, the line is a difference again and the cart is not green; with the tick back in place it matches again.
    Before, the stored match stayed green whatever the list said."""
    from aid.execution.by_hand import changes, mark, reopen

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    assert second["verdict"] == "match" and {entry["kind"] for entry in second["lines"]} == {"match"}
    assert reopen(prepared, a.ids, datetime(2026, 11, 13, 10, 0)) == "open"         # the tick taken back
    other = post(capture_server, html_c)[1]
    assert (other["case_id"], other["verdict"]) == (second["case_id"], "differences")
    assert _flag_of(other, "2") == ("regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (de lijst In Exact invoeren "
                                    "zegt nu iets anders)")
    assert other["message"] == "Inkooporder 250201: 2 regels komen overeen en 1 verschil"
    mark(prepared, a.ids, "entered", datetime(2026, 11, 13, 10, 30))                # ticked again: the match stands again
    again = post(capture_server, html_c)[1]
    assert again["verdict"] == "match" and {entry["kind"] for entry in again["lines"]} == {"match"}
    newer = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.15",
                                                   new_value="4.20"), "250201_20261113T110000", "R04", {}, datetime(2026, 11, 13, 11, 0))
    mark(prepared, [newer.entry_id], "entered", datetime(2026, 11, 13, 11, 5))      # another price entered for the line since
    changed = post(capture_server, html_c)[1]
    assert changed["verdict"] == "differences" and _flag_of(changed, "2").startswith("regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.20 op")
    assert server_module._TEXTS["en"]["other"] == "the list Enter in Exact now says otherwise"


def test_a_cart_checked_while_the_list_cannot_be_read_goes_to_the_buyer(capture_server, madeup_out, monkeypatch):
    """W6 at the pre-check: CART01's price is ticked; the same cart is shown on the next day while marks.jsonl cannot be read. The
    case is compared with the order as it was filed and prepares nothing: it goes to the buyer, the bar is amber, and one line
    without a line id says that the list could not be read, in Task 35's words."""
    from aid.execution.by_hand import changes, mark

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    rows = (prepared / "prepared_changes.csv").read_bytes()
    capture_server.server.deps = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared),
                                                                        "clock": lambda: datetime(2026, 11, 13, 9, 0)})
    html_c = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"}).replace("</body>", "<!-- shown 13-11 -->\n</body>")
    blind = _blind(capture_server, monkeypatch, html_c, "marks.jsonl")
    assert (blind["status"], blind["verdict"]) == ("review", "differences") and (prepared / "prepared_changes.csv").read_bytes() == rows
    assert [entry["text"] for entry in blind["lines"] if entry["kind"] == "review"] == ["de lijst In Exact invoeren kon niet worden gelezen"]
    assert _flag_of(blind, "2") == "regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder (verschil)"


# --- the second review round of Task 43: a stored match is judged whole (W19), a prepared change that left the list (W21), Task
# 35's rule for two captures that share one change (W23), and a try-out's tick (W24) ---

OTHER = "de lijst In Exact invoeren zegt nu iets anders"


def _export_of_cart_01(capture_server, madeup_out, tmp_path, name: str, price: str, when: datetime, other: str = "6.20") -> int:
    """A newer export of order 250201 with line 2 at that price and line 3 at `other` (6.20 as filed), given to the store at
    `when`, as the runner files one at the start of a pass: the number of looks the order has after it."""
    from aid.tests.helpers import filed_on, given_on

    store = capture_server.deps.store
    text = (madeup_out / "CART01_export.csv").read_text(encoding="utf-8")
    assert ",4.10," in text and ",6.20," in text
    path = tmp_path / f"{name}_export.csv"
    path.write_text(text.replace(",4.10,", f",{price},").replace(",6.20,", f",{other},"), encoding="utf-8")
    before = len(store.snapshots("250201"))
    store.from_export(path, {}, False, False)
    looks = len(store.snapshots("250201"))
    if looks > before:
        filed_on(store, "250201", f"e{looks}", when)
    else:
        given_on(store, "250201", f"e{looks}", when)
    return looks


def _ticked_cart(capture_server, madeup_out) -> tuple:
    """CART01 at 4,15 on 12 November (case A), its change ticked at 10:00, and the same cart on 13 November (case C), which matches
    through the ticked price: (A's page, C's page, C's answer, the entry ids of the ticked change, the Deps)."""
    from aid.execution.by_hand import changes, mark

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    assert second["verdict"] == "match" and second["case_id"] != first["case_id"]
    return html_a, html_c, second, a.ids, d


def test_two_captures_before_the_tick_share_one_change_and_its_put_back(capture_server, madeup_out, monkeypatch):
    """W23 (the check's finding 10): Task 35's rule K19 as it can still happen. The same cart is captured on 12 and on 13 November,
    both before any tick: one change with the entries of two cases. The buyer ticks it and then puts it back, which writes the
    undo into the write log of one of the two cases; the other case's log holds none. The pre-check judges each case's prepared
    write by that put-back on the list (_put_backs_of): while it waits both pages say so, and once it is entered both are a
    difference again, "(wijziging ongedaan gemaakt)". While the list cannot be read neither is green."""
    from aid.execution.by_hand import changes, mark
    from aid.review.hand_list import take_back

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    [run] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    assert (run.case_ids, run.state, len(run.ids)) == ([first["case_id"], second["case_id"]], "open", 2)
    assert first["verdict"] == second["verdict"] == "match" and "voorbereid" in second["message"]
    mark(prepared, run.ids, "entered", datetime(2026, 11, 13, 10, 0))
    for html in (html_c, html_a):
        assert post(capture_server, html)[1]["verdict"] == "match"
    later = d.model_copy(update={"clock": lambda: datetime(2026, 11, 13, 11, 0)})
    entered = next(change for change in changes(prepared)[0] if change.ids == run.ids)
    assert take_back(later, entered, datetime(2026, 11, 13, 11, 0), "nl").startswith("Nog niet teruggedraaid in Exact.")
    undos = [sum(1 for line in (prepared.parent / one["case_id"] / "write_log.jsonl").read_text(encoding="utf-8").splitlines()
                 if json.loads(line).get("undo_of")) for one in (first, second)]
    assert sorted(undos) == [0, 1]                                                  # one case's log holds the undo, the other's none
    for html in (html_c, html_a):
        waiting = post(capture_server, html)[1]
        assert "terugzetten staat op de lijst In Exact invoeren" in _flag_of(waiting, "2") and "ongedaan" not in json.dumps(waiting)
    assert _blind(capture_server, monkeypatch, html_c, "marks.jsonl")["verdict"] == "differences"
    [put] = [change for change in changes(prepared)[0] if change.puts_back is not None]
    mark(prepared, put.ids, "entered", datetime(2026, 11, 13, 12, 0))
    for html in (html_c, html_a):
        undone = post(capture_server, html)[1]
        assert undone["verdict"] == "differences" and _flag_of(undone, "2") == UNDONE


def test_a_stored_match_with_the_printed_order_is_judged_by_the_list(capture_server, madeup_out):
    """W19 (the check's finding 4, state a): CART01 as filed agrees with the printed order and is green. A confirmation then asks
    4.16 for line 2; the buyer enters it and ticks. The same cart, shown again the same day, is answered by the case that holds
    it, and that match is judged by the list as it is now: Exact holds 4.16, so the line is a difference and the cart is not
    green, as a new check of the cart would find. A value ticked before the look at the order was given changes nothing: the look
    is the newer word, and the read-only judgement ends a tick it contradicts."""
    from aid.execution.by_hand import mark
    from aid.tests.helpers import filed_on

    prepared = capture_server.settings.cases_root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared), "clock": lambda: datetime(2026, 11, 12, 9, 0)})
    capture_server.server.deps = d
    html = page(madeup_out, "CART01.html")
    first = post(capture_server, html)[1]
    assert (first["verdict"], first["message"]) == ("match", "Winkelwagen komt overeen met inkooporder 250201")
    asked = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.10",
                                                   new_value="4.16"), "250201_20261112T093000", "R04", {}, datetime(2026, 11, 12, 9, 30))
    still = post(capture_server, html)[1]                                         # asked, not entered: the order holds 4.10
    assert (still["case_id"], still["verdict"]) == (first["case_id"], "match")
    mark(prepared, [asked.entry_id], "entered", datetime(2026, 11, 12, 10, 0))
    marks = (prepared / "marks.jsonl").read_bytes()
    other = post(capture_server, html)[1]
    assert (other["case_id"], other["verdict"]) == (first["case_id"], "differences")
    assert _flag_of(other, "2") == f"regel 2 (KM-2102): prijs 4.10 in de winkelwagen, 4.16 op de inkooporder ({OTHER})"
    assert other["message"] == "Inkooporder 250201: 2 regels komen overeen en 1 verschil"
    filed_on(capture_server.deps.store, "250201", "e1", datetime(2026, 11, 12, 11, 0))      # the look given after the tick: 4.10 still
    assert post(capture_server, html)[1]["verdict"] == "match" and (prepared / "marks.jsonl").read_bytes() == marks


def test_a_stored_match_with_a_look_that_shows_the_entered_value_stays_green(capture_server, madeup_out, tmp_path):
    """W19, the other side: 4.15 is entered and ticked, and an export given after the tick shows it. The cart of the next day
    agrees with that look as it was printed. Shown again, its match is judged by the list: Exact holds 4.15, which is what the look
    shows, so the list says nothing else and the cart stays green."""
    from aid.execution.by_hand import changes, mark

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    assert _export_of_cart_01(capture_server, madeup_out, tmp_path, "after the entry", "4.15", datetime(2026, 11, 12, 11, 0)) == 2
    html_c, second, d = _cart_on(capture_server, madeup_out, 13)
    assert second["verdict"] == "match" and second["case_id"] != first["case_id"]
    assert not (prepared.parent / second["case_id"] / "steps" / "03_entered.json").exists()       # compared with the look as printed
    again = post(capture_server, html_c)[1]
    assert (again["case_id"], again["verdict"]) == (second["case_id"], "match") and {entry["kind"] for entry in again["lines"]} == {"match"}


@pytest.mark.parametrize("rows", ["other rows", "the same rows"])
def test_a_stored_match_does_not_wait_for_the_runner_to_end_a_tick(capture_server, madeup_out, tmp_path, rows):
    """W19 (the check's findings 4b and 8) and W16 at the pre-check (finding 6): the cart of 13 November matched through a tick
    given without the entry. An export given after the tick still shows 4.10; the runner files it at the start of a pass and brings
    the list in line only after the cases of that pass. In between, the stored match is already judged by the order as it stands:
    the tick counts as ended, in reading only, so the line is a difference and no mark is written. The same when the export has
    the rows of the look before, which files nothing. After the runner's pass the answer is the same, and the next day's cart is
    compared with the printed 4.10 again."""
    from aid.execution.by_hand import changes, reconcile

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, html_c, second, ids, d = _ticked_cart(capture_server, madeup_out)
    marks = (prepared / "marks.jsonl").read_bytes()
    other = "6.25" if rows == "other rows" else "6.20"
    looks = _export_of_cart_01(capture_server, madeup_out, tmp_path, "next morning", "4.10", datetime(2026, 11, 13, 10, 0), other)
    assert looks == (2 if rows == "other rows" else 1)
    amber = f"regel 2 (KM-2102): prijs 4.15 in de winkelwagen, 4.10 op de inkooporder ({OTHER})"
    before = post(capture_server, html_c)[1]                                      # the print filed, the runner's pass not yet run
    assert (before["case_id"], before["verdict"], _flag_of(before, "2")) == (second["case_id"], "differences", amber)
    assert (prepared / "marks.jsonl").read_bytes() == marks                        # the answer of a stored case writes no mark
    assert [change.state for change in changes(prepared)[0] if change.ids == ids] == ["entered"]
    assert reconcile(prepared, d.store, prepared.parent, datetime(2026, 11, 13, 10, 5)) == 0
    assert [change.state for change in changes(prepared)[0] if change.ids == ids] == ["open"]
    after = post(capture_server, html_c)[1]
    assert (after["verdict"], _flag_of(after, "2")) == ("differences", amber)
    html_d, third, _ = _cart_on(capture_server, madeup_out, 14)                    # the next day: a new check, against the printed 4.10
    assert third["case_id"] != second["case_id"] and third["message"].endswith("op de inkooporder voorbereid")
    assert [entry["po_value"] for entry in third["lines"] if entry["line_id"] == "2" and entry["field"] == "price"] == ["4.10"]


def test_no_stored_match_is_green_while_the_list_cannot_be_read(capture_server, madeup_out, monkeypatch):
    """W19 (the check's finding 4, state c): CART01 as filed agreed with the printed order. While a file of the list cannot be
    read, the case that holds that match cannot be judged by the list, and a new check of the cart would go to the buyer: the
    answer is amber and says, once, that the list could not be read. So also when the list cannot be understood. Readable again,
    the match is green again."""
    from aid.execution.by_hand import mark

    prepared = capture_server.settings.cases_root / "prepared"
    d = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared), "clock": lambda: datetime(2026, 11, 12, 9, 0)})
    capture_server.server.deps = d
    html = page(madeup_out, "CART01.html")
    first = post(capture_server, html)[1]
    assert first["verdict"] == "match" and {entry["kind"] for entry in first["lines"]} == {"match"}
    asked = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250202", version=1, line_id="1", old_value="12.50",
                                                   new_value="12.60"), "250202_20261112T093000", "R04", {}, datetime(2026, 11, 12, 9, 30))
    mark(prepared, [asked.entry_id], "entered", datetime(2026, 11, 12, 10, 0))       # another order's change: the list's two files exist
    for name in ("prepared_changes.csv", "marks.jsonl"):
        blind = _blind(capture_server, monkeypatch, html, name)
        assert (blind["case_id"], blind["verdict"]) == (first["case_id"], "differences"), name
        assert [entry["text"] for entry in blind["lines"] if entry["kind"] == "review"] == ["de lijst In Exact invoeren kon niet worden gelezen"]
        assert blind["message"] == "Inkooporder 250201: 3 regels komen overeen en 1 reden voor de inkoper", name
        assert {entry["kind"] for entry in blind["lines"]} == {"match", "review"}
    rows = prepared / "prepared_changes.csv"
    good = rows.read_bytes()
    rows.write_bytes(good.replace(b",", b";"))                                      # saved from a spreadsheet: not understood
    resaved = post(capture_server, html)[1]
    assert resaved["verdict"] == "differences" and [entry["kind"] for entry in resaved["lines"]].count("review") == 1
    rows.write_bytes(good)
    assert post(capture_server, html)[1]["verdict"] == "match"


def test_a_prepared_change_that_a_case_took_off_the_list_is_no_longer_prepared(capture_server, madeup_out):
    """W21 (the check's finding 7): 4.15 is entered and ticked. The cart at 4,20 (case X) is green with a change from 4.15 to 4.20
    prepared. The cart at 4,15 again (case Y) names the value Exact holds, so X's open change leaves the list (W4). X's page shown
    again is no longer green with "voorbereid": nothing asks the buyer to enter 4.20 any more, so its line is a difference again.
    Before, the bar stayed green while the list held no row for the line."""
    from aid.execution.by_hand import changes, mark

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, first, d = _cart_on(capture_server, madeup_out, 12)
    [a] = [change for change in changes(prepared)[0] if change.kind == "po_line_price" and change.po_number == "250201"]
    mark(prepared, a.ids, "entered", datetime(2026, 11, 12, 10, 0))
    html_x = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,20"}).replace("</body>", "<!-- shown 14-11 -->\n</body>")
    _, x, d = _cart_on(capture_server, madeup_out, 14, html_x)
    assert (x["verdict"], x["message"]) == ("match", "Winkelwagen komt overeen met inkooporder 250201; 1 regel op de inkooporder voorbereid")
    _, y, d = _cart_on(capture_server, madeup_out, 15)                              # the cart at 4,15 again
    assert y["verdict"] == "match"
    states = [(change.old_value, change.new_value, change.state, change.withdrawn) for change in changes(prepared)[0]
              if change.kind == "po_line_price" and change.po_number == "250201"]
    assert states == [("4.10", "4.15", "entered", False), ("4.15", "4.20", "replaced", True)]
    again = post(capture_server, html_x)[1]
    assert (again["case_id"], again["verdict"]) == (x["case_id"], "differences")
    assert _flag_of(again, "2") == f"regel 2 (KM-2102): prijs 4.20 in de winkelwagen, 4.15 op de inkooporder ({OTHER})"
    assert again["message"] == "Inkooporder 250201: 2 regels komen overeen en 1 verschil"
    assert server_module._TEXTS["en"]["other"] == "the list Enter in Exact now says otherwise"


def test_a_try_outs_tick_holds_no_stored_match_of_a_suppliers_cart(capture_server, madeup_out, tmp_path):
    """W24 (the check's mutant C20): the cart of 13 November matched through the buyer's tick of 4.15. The buyer takes that tick
    back; a change of a try-out for the same line and value is ticked. A try-out's tick counts in a case made from a try-out
    alone: the stored match of the supplier's cart is a difference again."""
    from aid.case import open_case
    from aid.execution.by_hand import changes, mark, reopen
    from aid.madeup.generate import BOLD, Row, Text
    from aid.tryout_mark import FIRST_ROW

    prepared = capture_server.settings.cases_root / "prepared"
    html_a, html_c, second, ids, d = _ticked_cart(capture_server, madeup_out)
    document = tmp_path / "PROEF orderbevestiging.pdf"
    document.write_bytes(pdf_bytes([[Row([Text(0, FIRST_ROW, BOLD, 14)]), Row([Text(0, "Orderbevestiging")])]], "made-up try-out"))
    tried = open_case(prepared.parent, "confirmation", [document], "250201", datetime(2026, 11, 13, 9, 30))
    made = PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250201", version=1, line_id="2", old_value="4.10",
                                                  new_value="4.15"), tried.case_id, "R04", {}, datetime(2026, 11, 13, 9, 30))
    assert reopen(prepared, ids, datetime(2026, 11, 13, 10, 0)) == "open"
    mark(prepared, [made.entry_id], "entered", datetime(2026, 11, 13, 10, 5))
    assert [(change.tryout, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [(False, "open"), (True, "entered")]
    answer = post(capture_server, html_c)[1]
    assert (answer["case_id"], answer["verdict"]) == (second["case_id"], "differences") and OTHER in _flag_of(answer, "2")
# --- which earlier case answers a cart, and the replacement of a case (Task 41, rules 2 to 5; ruling Q2 of the review round) ---

def test_a_grey_cart_is_checked_again_once_its_order_is_filed(capture_server, tmp_path):
    """Rule 2: an earlier case of the same page answers only while it has a PO or no PO is found now. A cart filled before the export
    of its order was filed is grey; once the export is filed, the same page opens a new case for that order, one second after the
    grey one, and the grey case is finished as replaced (finish_replaced of aid/review/close.py) and marked in its capture.json.
    The same page a third time answers from the new case. So does the same cart in a page whose invisible parts changed."""
    r = capture_server
    html = cart_page()
    grey = post(r, html)[1]
    assert (grey["verdict"], grey["po_number"], grey["case_id"]) == ("no_po", None, "unknown_20261112T090000")
    assert open_cases(r) == [grey["case_id"]] and "replaced_by" not in capture_meta(r, grey["case_id"])
    exported(r, tmp_path, "order.csv", {"250981": OWN})
    found = post(r, html)[1]
    assert (found["verdict"], found["po_number"], found["case_id"]) == ("match", "250981", "250981_20261112T090001")
    assert found["message"] == f"Winkelwagen komt overeen met inkooporder 250981{CHOSEN_NL}"
    old = r.settings.cases_root / grey["case_id"]
    assert capture_meta(r, grey["case_id"])["replaced_by"] == found["case_id"]
    assert set(capture_meta(r, grey["case_id"])) == {"sha256", "cart_digest", "lines_digest", "host", "page", "replaced_by"}
    result = result_of(r, grey["case_id"])
    assert (result["status"], result["supplier"]) == ("closed", None)
    [record] = close_records(r, grey["case_id"])
    assert (record["action"], record["by"]) == ("replaced", found["case_id"])
    assert json.loads((old / "audit.json").read_text(encoding="utf-8"))["close"] == [record]
    assert open_cases(r) == [] and case_folders(r) == sorted([found["case_id"], grey["case_id"]])
    assert len(captures(r)) == 2                                               # the page of each case
    third = post(r, html)[1]
    assert third == found and len(case_folders(r)) == 2 and len(captures(r)) == 2
    changed = post(r, cart_page(note="<!-- rendered 09:00:03 -->"))[1]          # the same cart, other bytes: the new case answers
    assert changed == found and len(case_folders(r)) == 2
    assert close_records(r, grey["case_id"]) == [record]                        # replaced once: it is no earlier case any more
    # a grey case of a cart is replaced through the same cart in another page as well, and a stated number is found by the number
    stated = cart_page(("KM-9003", "KM-9004"), reference="250982")
    grey_two = post(r, stated)[1]
    assert grey_two["verdict"] == "no_po"
    exported(r, tmp_path, "second.csv", {"250982": ("KM-9003", "KM-9004")})
    found_two = post(r, stated.replace("<h1>", "<!-- again --><h1>"))[1]
    assert (found_two["po_number"], found_two["message"]) == ("250982", "Winkelwagen komt overeen met inkooporder 250982")
    assert capture_meta(r, grey_two["case_id"])["replaced_by"] == found_two["case_id"] and post(r, stated)[1] == found_two


def test_while_no_order_is_found_the_earlier_answer_repeats(capture_server):
    """Rule 4: while no PO is found, the earlier answer repeats and no new case is opened, for the same page and for the same cart in
    a page whose invisible parts changed."""
    r = capture_server
    grey = post(r, cart_page())[1]
    assert grey["verdict"] == "no_po" and post(r, cart_page())[1] == grey
    assert post(r, cart_page(note="<!-- rendered 09:00:02 -->"))[1] == grey
    assert case_folders(r) == [grey["case_id"]] and len(captures(r)) == 1 and close_records(r, grey["case_id"]) == []
    assert "replaced_by" not in capture_meta(r, grey["case_id"]) and open_cases(r) == [grey["case_id"]]


def test_a_replaced_case_answers_nothing(capture_server, tmp_path):
    """Rule 3: a replaced capture case answers no capture and is not offered on the review page, also when the case that replaced it
    is gone: the same page then opens a new case, and the same cart in another page too. The close of the review page refuses it."""
    r = capture_server
    html = cart_page(prices={"KM-9002": "44,00"})                              # 10 percent: a difference for the buyer
    grey = post(r, html)[1]
    exported(r, tmp_path, "order.csv", {"250991": OWN})
    found = post(r, html)[1]
    assert (found["verdict"], found["status"]) == ("differences", "review") and open_cases(r) == [found["case_id"]]
    replaced_case = load_case(r.settings.cases_root, grey["case_id"])
    with pytest.raises(ValueError, match="is not in review"):
        close_case(replaced_case, r.deps)
    shutil.rmtree(r.settings.cases_root / found["case_id"])                    # the case that replaced it is gone
    again = post(r, html)[1]
    assert again["case_id"] not in (grey["case_id"],) and again["po_number"] == "250991" and again["verdict"] == "differences"
    assert capture_meta(r, grey["case_id"])["replaced_by"] == found["case_id"]              # as it was marked
    other = post(r, cart_page(prices={"KM-9002": "44,00"}, note="<!-- other bytes -->"))[1]
    assert other == again and grey["case_id"] not in open_cases(r)
    assert len(close_records(r, grey["case_id"])) == 1                          # and it is not replaced a second time
    # so it is for a replaced guess: it does not answer again when the case that replaced it is gone
    items = ("KM-9003", "KM-9004")
    released(r, tmp_path, "250992", items, at=EIGHT - DAY)
    page_two = cart_page(items)
    guess = post(r, page_two)[1]
    assert texts_of(guess) == [SENT_NL.format(po="250992")]
    exported(r, tmp_path, "new.csv", {"250993": items})
    newer = post(r, page_two)[1]
    assert newer["po_number"] == "250993" and capture_meta(r, guess["case_id"])["replaced_by"] == newer["case_id"]
    shutil.rmtree(r.settings.cases_root / newer["case_id"])
    once_more = post(r, page_two)[1]
    assert (once_more["po_number"], once_more["verdict"]) == ("250993", "match") and once_more["case_id"] != guess["case_id"]
    assert len(close_records(r, guess["case_id"])) == 1 and guess["case_id"] not in open_cases(r)
    # and so it is while its capture.json cannot be read: its close record says that it was replaced
    held = r.settings.cases_root / guess["case_id"] / "capture.json"
    whole = held.read_bytes()
    held.write_bytes(b'{"sha256": "torn')
    shutil.rmtree(r.settings.cases_root / once_more["case_id"])
    unread = post(r, page_two)[1]
    assert (unread["po_number"], unread["verdict"]) == ("250993", "match") and unread["case_id"] != guess["case_id"]
    assert held.read_bytes() == b'{"sha256": "torn' and len(close_records(r, guess["case_id"])) == 1
    held.write_bytes(whole)


def test_a_new_case_that_failed_replaces_nothing(capture_server, tmp_path):
    """Only a case that can answer replaces an earlier one: when the run of the new case fails, the grey case stays as it is, on the
    review page too, and the next capture of the page, whose run succeeds, opens the case that replaces it."""
    r = capture_server

    def failing(path, kind):
        raise ReaderError("made-up reader failure")
    html = cart_page()
    grey = post(r, html)[1]
    exported(r, tmp_path, "order.csv", {"250985": OWN})
    healthy = r.server.deps
    r.server.deps = healthy.model_copy(update={"reader": failing})
    failed = post(r, html)[1]
    assert (failed["verdict"], failed["po_number"]) == ("failed", "250985") and failed["case_id"] != grey["case_id"]
    assert "replaced_by" not in capture_meta(r, grey["case_id"]) and open_cases(r) == [grey["case_id"]]
    r.server.deps = healthy
    found = post(r, html)[1]
    assert found["verdict"] == "match" and found["case_id"] not in (grey["case_id"], failed["case_id"])
    assert capture_meta(r, grey["case_id"])["replaced_by"] == found["case_id"] and open_cases(r) == []
    assert "replaced_by" not in capture_meta(r, failed["case_id"]) and post(r, html)[1] == found


def test_an_earlier_case_whose_capture_json_cannot_be_read_is_left_as_it_is(capture_server, tmp_path):
    """The mark is added to what capture.json holds, so a file that cannot be read is never written over: the new case answers, the
    earlier case is left as it is, and it is replaced the next time the page comes once its file can be read again."""
    r = capture_server
    html = cart_page()
    grey = post(r, html)[1]
    exported(r, tmp_path, "order.csv", {"250988": OWN})
    held = r.settings.cases_root / grey["case_id"] / "capture.json"
    whole = held.read_bytes()
    for torn in (b'{"sha256": "torn', b'["no", "object"]'):
        held.write_bytes(torn)
        status, found = post(r, html)
        assert status == 200 and (found["po_number"], found["verdict"]) == ("250988", "match")
        assert held.read_bytes() == torn and close_records(r, grey["case_id"]) == [] and open_cases(r) == [grey["case_id"]]
    held.write_bytes(whole)
    assert post(r, html)[1] == found and capture_meta(r, grey["case_id"]) == json.loads(whole) | {"replaced_by": found["case_id"]}
    assert open_cases(r) == [] and len(case_folders(r)) == 2


def test_a_decision_saved_while_the_new_case_runs_stands(capture_server, tmp_path, monkeypatch):
    """Q2 (a) and (c), with ruling N22 of Task 36: a guess is read again right before it is finished as replaced. A decision that
    the buyer saved on it while the new case was opened and run stands: the guess is left as it is, not marked, on the review page
    too, and the new case, which has run, answers the page from then on, and the same cart in a page of other bytes as well: of the
    cases of the page and of the cart together, the one opened last decides."""
    r = capture_server
    released(r, tmp_path, "250986", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    assert texts_of(guess)[0] == SENT_NL.format(po="250986") and open_cases(r) == [guess["case_id"]]
    exported(r, tmp_path, "new.csv", {"250987": OWN})
    real = server_module.run_case

    def decided_meanwhile(case, d):
        old, [item] = _items(r, guess["case_id"])
        decide(old, item["item_id"], "keep")                                    # the buyer decides while the new case runs
        return real(case, d)
    monkeypatch.setattr(server_module, "run_case", decided_meanwhile)
    found = post(r, html)[1]
    monkeypatch.undo()
    assert (found["po_number"], found["case_id"]) == ("250987", "250987_20261112T090001")
    assert "replaced_by" not in capture_meta(r, guess["case_id"]) and close_records(r, guess["case_id"]) == []
    assert result_of(r, guess["case_id"])["status"] == "review" and open_cases(r) == [guess["case_id"], found["case_id"]]
    assert post(r, html)[1] == found and len(case_folders(r)) == 2                          # the case opened last answers
    assert post(r, cart_page(prices={"KM-9002": "44,00"}, note="<!-- other bytes -->"))[1] == found and len(case_folders(r)) == 2


def test_a_guessed_cart_is_checked_again_when_another_order_is_found(capture_server, tmp_path):
    """Q2 (c): an earlier case of the same page whose order was a guess, and on which the buyer did nothing, answers only while the
    order found now is the same order and still a guess. When another order is found for sure (the export of the cart's own order
    was filed meanwhile), when another order is the guess now, or when the guess's own order is now found for sure, the same page
    opens a new case, and the guess is finished as replaced: the bar never says what is no longer so."""
    r = capture_server
    released(r, tmp_path, "250960", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9001": "2,52"})
    guess = post(r, html)[1]
    assert (guess["po_number"], guess["case_id"], texts_of(guess)[0]) == ("250960", "250960_20261112T090000", SENT_NL.format(po="250960"))
    assert post(r, html)[1] == guess and case_folders(r) == [guess["case_id"]]             # the same order, still a guess: it answers
    exported(r, tmp_path, "new.csv", {"250961": OWN})                          # the export of the new order, filed meanwhile
    found = post(r, html)[1]
    assert (found["po_number"], found["case_id"], found["verdict"]) == ("250961", "250961_20261112T090001", "match")
    assert found["message"] == f"Winkelwagen komt overeen met inkooporder 250961{CHOSEN_NL}; 1 regel op de inkooporder voorbereid"
    assert capture_meta(r, guess["case_id"])["replaced_by"] == found["case_id"] and result_of(r, guess["case_id"])["status"] == "closed"
    assert [(record["action"], record["by"]) for record in close_records(r, guess["case_id"])] == [("replaced", found["case_id"])]
    assert open_cases(r) == [] and post(r, html)[1] == found and len(case_folders(r)) == 2
    # another order is the guess now: a new guess, and the earlier one is replaced
    items = ("KM-9003", "KM-9004")
    exported(r, tmp_path, "earlier.csv", {"250962": items}, at=EIGHT - DAY)
    page_two = cart_page(items, prices={"KM-9003": "0,25"})
    first = post(r, page_two)[1]
    assert (first["po_number"], texts_of(first)[0]) == ("250962", NOT_SURE_NL.format(po="250962"))
    exported(r, tmp_path, "more.csv", {"250963": (*items, "KM-9005")})         # an order of today with one item more
    second = post(r, page_two)[1]
    assert (second["po_number"], second["case_id"]) == ("250963", "250963_20261112T090001")
    assert texts_of(second)[0] == NOT_SURE_NL.format(po="250963") and load_case(r.settings.cases_root, second["case_id"]).po_found_by == NOT_SURE
    assert capture_meta(r, first["case_id"])["replaced_by"] == second["case_id"] and open_cases(r) == [second["case_id"]]
    assert post(r, page_two)[1] == second and len(case_folders(r)) == 4
    # the guess's own order is now found for sure (exported again today, with a change): a found case takes its place
    pair = ("KM-9005", "KM-9006")
    exported(r, tmp_path, "pair.csv", {"250964": pair}, at=EIGHT - DAY)
    page_three = cart_page(pair)
    unsure = post(r, page_three)[1]
    assert (unsure["po_number"], unsure["case_id"], texts_of(unsure)) == ("250964", "250964_20261112T090000", [NOT_SURE_NL.format(po="250964")])
    exported(r, tmp_path, "pair_again.csv", {"250964": pair}, suppliers={"250964": "S22"})
    assert order_of(r, tmp_path, page_three) == ("250964", BY_ITEMS)
    sure = post(r, page_three)[1]
    assert (sure["po_number"], sure["case_id"], sure["verdict"]) == ("250964", "250964_20261112T090001", "match")
    assert sure["message"] == f"Winkelwagen komt overeen met inkooporder 250964{CHOSEN_NL}" and texts_of(sure) == []
    assert capture_meta(r, unsure["case_id"])["replaced_by"] == sure["case_id"] and result_of(r, unsure["case_id"])["status"] == "closed"
    # the same order and still a guess, of the other kind now that its PDF came in: the earlier guess answers, with the first line
    # of the kind found now (ruling Q12) and nothing else changed
    duo = ("KM-9006", "KM-9007")
    exported(r, tmp_path, "duo.csv", {"250965": duo}, at=EIGHT - DAY)
    page_four = cart_page(duo)
    kept = post(r, page_four)[1]
    assert texts_of(kept) == [NOT_SURE_NL.format(po="250965")]
    released(r, tmp_path, "250965", duo, at=EIGHT + 60)
    assert order_of(r, tmp_path, page_four) == ("250965", ALREADY_SENT)
    again = post(r, page_four)[1]
    assert texts_of(again) == [SENT_NL.format(po="250965")] and "replaced_by" not in capture_meta(r, kept["case_id"])
    assert again["lines"][1:] == kept["lines"][1:] and {name: value for name, value in again["lines"][0].items() if name != "text"} == {
        name: value for name, value in kept["lines"][0].items() if name != "text"}
    assert {name: value for name, value in again.items() if name != "lines"} == {name: value for name, value in kept.items() if name != "lines"}
    assert len(case_folders(r)) == 7


def test_a_guess_the_buyer_decided_on_stands(capture_server, tmp_path):
    """Q2 (a) and (b) with ruling Q18: a guess on which the buyer decided is never replaced and never changed. While its order is
    still the guess it answers as it is, and once the buyer closed it the bar no longer repeats its first line: the buyer checked
    the order. When another order is found later, a case is opened for that order beside it, and the guess keeps its files."""
    r = capture_server
    other = ("KM-9003", "KM-9004")
    released(r, tmp_path, "250970", other, at=EIGHT - DAY)
    html = cart_page(other, prices={"KM-9003": "0,25"})
    decided = post(r, html)[1]
    assert texts_of(decided)[0] == SENT_NL.format(po="250970")
    _decide_all(r, decided["case_id"], "keep")
    left = folder_bytes(r, decided["case_id"])
    again = post(r, html)[1]
    assert (again["case_id"], again["po_number"], again["status"]) == (decided["case_id"], "250970", "closed")
    assert texts_of(again) == ["regel 1 (KM-9003): prijs 0.25 in de winkelwagen, 0.20 op de inkooporder (verschil); besluit van de inkoper: "
                               "waarde van de inkooporder gehouden"]           # the buyer checked the order: its line is not repeated
    assert folder_bytes(r, decided["case_id"]) == left and case_folders(r) == [decided["case_id"]]
    exported(r, tmp_path, "later.csv", {"250971": other})
    assert order_of(r, tmp_path, html) == ("250971", BY_ITEMS)
    later = post(r, html)[1]
    assert (later["po_number"], later["case_id"]) == ("250971", "250971_20261112T090001") and CHOSEN_NL in later["message"]
    assert folder_bytes(r, decided["case_id"]) == left and "replaced_by" not in capture_meta(r, decided["case_id"])
    assert close_records(r, decided["case_id"])[-1]["action"] == "close" and len(case_folders(r)) == 2


def test_the_first_line_of_a_guess_that_answers_is_that_of_the_kind_found_now(tmp_path, madeup_out):
    """Ruling Q12: a guess answers while its own order is still the guess, and its first line is the sentence of the kind the rule
    gives now, not that of the kind saved with the case. A cart that was "not sure" for an order whose PDF came in since says that
    the order was sent already; the case is the same one, and nothing of it is written. The other way round: the order of a guess
    "already sent" whose confirmation turned out to have failed is "not sure" at the next capture."""
    for language, not_sure, sent in (("nl", NOT_SURE_NL, SENT_NL), ("en", NOT_SURE_EN, SENT_EN)):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            exported(r, tmp_path / language, "earlier.csv", {"250960": OWN}, at=EIGHT - DAY)
            html = cart_page(prices={"KM-9002": "44,00"})
            guess = post(r, html)[1]
            assert texts_of(guess)[0] == not_sure.format(po="250960") and order_of(r, tmp_path, html) == ("250960", NOT_SURE)
            before = folder_bytes(r, guess["case_id"])
            assert json.loads(before["case.json"])["po_found_by"] == NOT_SURE
            released(r, tmp_path / language, "250960", OWN, at=EIGHT - DAY)     # the order's PDF comes in: it was sent
            assert order_of(r, tmp_path, html) == ("250960", ALREADY_SENT)
            for page_html in (html, html.replace("<h1>", "<!-- other bytes --><h1>")):
                again = post(r, page_html)[1]
                assert again["case_id"] == guess["case_id"] and texts_of(again)[0] == sent.format(po="250960"), language
                assert texts_of(again)[1:] == texts_of(guess)[1:] and again["message"] == guess["message"]
            assert folder_bytes(r, guess["case_id"]) == before and case_folders(r) == [guess["case_id"]]      # nothing written
            assert prepared_rows(r) == [] and open_cases(r) == [guess["case_id"]]
        with running(tmp_path / (language + "_back"), madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            exported(r, tmp_path / language, "earlier_two.csv", {"250962": OWN}, at=EIGHT - DAY)
            reply = confirmed(r, "250962")
            html = cart_page(prices={"KM-9002": "44,00"})
            guess = post(r, html)[1]
            assert texts_of(guess)[0] == sent.format(po="250962")
            result = reply.root / "steps" / "08_result.json"
            result.write_text(json.dumps(json.loads(result.read_text(encoding="utf-8")) | {"status": "failed"}), encoding="utf-8")
            again = post(r, html)[1]
            assert again["case_id"] == guess["case_id"] and texts_of(again)[0] == not_sure.format(po="250962"), language
        with running(tmp_path / (language + "_none"), madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            released(r, tmp_path / language, "250963", OWN, at=EIGHT - DAY)     # a guess "already sent", and then no order is found at all
            html = cart_page(prices={"KM-9002": "44,00"})
            guess = post(r, html)[1]
            assert texts_of(guess)[0] == sent.format(po="250963")
            shutil.rmtree(r.deps.store.root / "250963")
            assert order_of(r, tmp_path, html) == (None, None)
            assert post(r, html)[1] == guess                                    # the line of the kind saved with the case


def test_a_guess_the_buyer_acted_on_answers_only_while_its_order_is_still_the_guess(capture_server, tmp_path):
    """Ruling Q18, which amends Q2 (a): for every guess, also one the buyer acted on, the rule of the cart's order is asked again
    when its page or its cart comes. The buyer chose "Onopgelost" on a guess, gave the export of the cart's own order and reloaded:
    a new case is opened for that order, found by the items, and its bar is shown. The guess he acted on is never replaced and
    never changed: its files are what they were, and it is still on the review page. While the order found now is still the
    case's guess, or none is found, the guess answers, as a guess that he confirmed and closed does."""
    r = capture_server
    released(r, tmp_path, "250970", OWN, at=EIGHT - DAY)                        # only an order that was sent: a guess
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    assert texts_of(guess)[0] == SENT_NL.format(po="250970") and guess["status"] == "review"
    case, items = _items(r, guess["case_id"])
    for item in items:
        decide(case, item["item_id"], "unresolved")
    assert close_case(case, r.deps).status == "review"
    left = folder_bytes(r, guess["case_id"])
    still = post(r, html)[1]                                                    # its order is still the guess: the guess answers
    assert still["case_id"] == guess["case_id"] and texts_of(still)[0] == SENT_NL.format(po="250970")
    exported(r, tmp_path, "own.csv", {"250971": OWN})                           # the export of the cart's own order, then the reload
    assert order_of(r, tmp_path, html) == ("250971", BY_ITEMS)
    found = post(r, html)[1]
    assert (found["po_number"], found["case_id"], found["status"]) == ("250971", "250971_20261112T090001", "review")
    assert CHOSEN_NL in found["message"] and not any(text.startswith("De inkooporder die het best") for text in texts_of(found))
    assert texts_of(found) == ["regel 2 (KM-9002): prijs 44.00 in de winkelwagen, 40.00 op de inkooporder (prijsverschil boven de drempel)"]
    assert folder_bytes(r, guess["case_id"]) == left and "replaced_by" not in capture_meta(r, guess["case_id"])
    assert sorted(open_cases(r)) == sorted([guess["case_id"], found["case_id"]])                 # the guess stays on the review page
    for page_html in (html, html.replace("<h1>", "<!-- other bytes --><h1>")):
        assert post(r, page_html)[1] == found and len(case_folders(r)) == 2     # the new case answers the page and the cart from now on
    assert folder_bytes(r, guess["case_id"]) == left
    # a guess confirmed and closed, the same page again: the closed case answers
    released(r, tmp_path, "250980", ("KM-9003", "KM-9004"), at=EIGHT - DAY)
    html_two = cart_page(("KM-9003", "KM-9004"), prices={"KM-9003": "0,25"})
    closed = post(r, html_two)[1]
    _decide_all(r, closed["case_id"], "confirm")
    assert result_of(r, closed["case_id"])["status"] == "closed"
    left_two = folder_bytes(r, closed["case_id"])
    again = post(r, html_two)[1]
    assert (again["case_id"], again["status"], again["po_number"]) == (closed["case_id"], "closed", "250980")
    assert not any(text.startswith("De inkooporder die het best") for text in texts_of(again))    # he checked the order
    assert folder_bytes(r, closed["case_id"]) == left_two and len(case_folders(r)) == 3


def test_a_press_of_the_close_button_that_closed_nothing_is_no_act(tmp_path, madeup_out):
    """Q2 (a) and (b), the review's blocking finding: a press of the close button that left the guess in review and carried nothing
    out, with no item decided, is no act of the buyer. The bar keeps the first line of the guess, with its reason in the summary,
    and the guess is still checked again: once the export of the cart's own order is filed, the reload that the line itself asks
    for opens a case for that order, and the guess is finished as replaced."""
    for language, sentence in (("nl", SENT_NL), ("en", SENT_EN)):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            released(r, tmp_path / language, "250960", OWN, at=EIGHT - DAY)
            html = cart_page(prices={"KM-9001": "2,52"})
            guess = post(r, html)[1]
            assert texts_of(guess)[0] == sentence.format(po="250960") and "1 re" in guess["message"]
            case = load_case(r.settings.cases_root, guess["case_id"])
            said, still_open = close_text(case, r.deps, language)                # the page's close button, nothing decided
            assert still_open and said == STRINGS[language]["new_status"].format(status=STRINGS[language]["status:review"])
            [record] = close_records(r, guess["case_id"])
            assert (record["action"], record["status"], record["carried_out"], record["write_entries"], record["messages"]) == (
                "close", "review", {}, [], [])
            assert post(r, html)[1] == guess and open_cases(r) == [guess["case_id"]]            # its line and its summary stay
            exported(r, tmp_path / language, "new.csv", {"250961": OWN})
            found = post(r, html)[1]                                             # and it is still checked again
            assert (found["po_number"], found["verdict"]) == ("250961", "match") and found["case_id"] != guess["case_id"]
            assert capture_meta(r, guess["case_id"])["replaced_by"] == found["case_id"] and open_cases(r) == []
            assert [record["action"] for record in close_records(r, guess["case_id"])] == ["close", "replaced"]


@pytest.mark.parametrize("act", ["nothing", "close press", "decision", "write", "message", "undo", "close that closed", "close that carried out",
                                 "close that wrote", "close that sent"])
def test_each_act_on_a_guess_makes_it_stand(capture_server, tmp_path, act):
    """Q2 (a), clause by clause, on a guess whose files hold one trace alone: a decision of the buyer, a line in the write log, a
    message sent, an undo, a close that closed the case and a close that carried something out (an item, a write or a message in
    its record) each make the guess stand: it is not replaced and not changed when the cart's own order is found. A guess without any of them, and one with only a press of the
    close button that left it in review and carried nothing out, is replaced. Either way the rule is asked again and a case is
    opened for the order found now (ruling Q18)."""
    r = capture_server
    released(r, tmp_path, "250960", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    root = r.settings.cases_root / guess["case_id"]
    pressed = {"action": "close", "at": "2026-11-12T09:00:01", "decisions": {}, "carried_out": {}, "write_entries": [], "messages": [],
               "status": "review", "error": None, "stop": None, "reapproval_pending": None, "settled": [], "question_open": False}
    decision = {"item_id": "RI1", "decision": "unresolved", "article_update": False, "at": "2026-11-12T09:00:01"}
    records = {"close press": [pressed], "undo": [{"action": "undo", "at": "2026-11-12T09:00:01", "entry_id": "made-up", "undo": {}}],
               "close that closed": [pressed | {"status": "closed"}], "close that carried out": [pressed | {"carried_out": {"RI1": decision}}],
               "close that wrote": [pressed | {"write_entries": [{"entry_id": "made-up"}]}],
               "close that sent": [pressed | {"messages": [{"template": "reapproval", "status": "sent"}]}]}
    if act in records:
        (root / "steps" / "09_close.json").write_text(json.dumps({"records": records[act]}), encoding="utf-8")
    elif act == "decision":
        (root / "decisions.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")
    elif act == "write":
        (root / "write_log.jsonl").write_text('{"entry_id": "made-up"}\n', encoding="utf-8")
    elif act == "message":
        (root / "messages" / "sent.jsonl").write_text("", encoding="utf-8")
    left = folder_bytes(r, guess["case_id"])
    exported(r, tmp_path, "new.csv", {"250961": OWN})
    assert order_of(r, tmp_path, html) == ("250961", BY_ITEMS)
    answer = post(r, html)[1]
    assert (answer["po_number"], answer["case_id"]) == ("250961", "250961_20261112T090001") and len(case_folders(r)) == 2, act
    if act in ("nothing", "close press"):
        assert capture_meta(r, guess["case_id"])["replaced_by"] == answer["case_id"], act
        assert result_of(r, guess["case_id"])["status"] == "closed", act
    else:
        assert "replaced_by" not in capture_meta(r, guess["case_id"]) and folder_bytes(r, guess["case_id"]) == left, act


def test_a_guess_with_one_item_decided_keeps_its_line_and_is_never_replaced(tmp_path, madeup_out):
    """Q2 (a) and (b), the review's findings 1 and 7 (its s_partial_close): the buyer decided one item of three on a guess and
    pressed close, which left the case in review. Its first line stays in the bar while the saved result is in review, and goes
    once the buyer closed the case. The decision is an act: the guess is never replaced and never changed, also when the cart's
    own order is found for sure later, for which a case of its own is then opened (ruling Q18)."""
    for language, sentence, reason in (("nl", SENT_NL, "1 reden voor de inkoper"), ("en", SENT_EN, "1 reason for the buyer")):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            released(r, tmp_path / language, "260001", (*OWN, "KM-9003"), at=EIGHT - DAY)
            html = cart_page(prices={"KM-9001": "2,75", "KM-9002": "44,00"})
            first = post(r, html)[1]
            assert texts_of(first)[0] == sentence.format(po="260001") and first["status"] == "review" and reason in first["message"]
            case, items = _items(r, first["case_id"])
            assert len(items) == 3
            decide(case, items[0]["item_id"], "keep")                            # one of three items decided, the others left open
            assert close_case(case, r.deps).status == "review" and open_cases(r) == [first["case_id"]]
            [record] = close_records(r, first["case_id"])
            assert (record["status"], list(record["carried_out"])) == ("review", [items[0]["item_id"]])
            second = post(r, html)[1]
            assert (second["case_id"], second["status"], texts_of(second)[0]) == (first["case_id"], "review", sentence.format(po="260001"))
            assert second["message"] == first["message"] and second["lines"][0] == first["lines"][0]
            assert post(r, html.replace("<h1>", "<!-- other bytes --><h1>"))[1] == second and case_folders(r) == [first["case_id"]]
            for item in items[1:]:
                decide(case, item["item_id"], "keep")
            assert close_case(case, r.deps).status == "closed"
            closed = post(r, html)[1]
            assert (closed["case_id"], closed["status"]) == (first["case_id"], "closed") and reason not in closed["message"]
            assert not [entry for entry in closed["lines"] if entry["kind"] == "review"]
            left = folder_bytes(r, first["case_id"])
            exported(r, tmp_path / language, "own.csv", {"260002": OWN})         # the cart's own order: found for sure now
            assert order_of(r, tmp_path, html) == ("260002", BY_ITEMS)
            found = post(r, html)[1]                                             # a case for that order; the guess is never replaced
            assert (found["po_number"], found["case_id"]) == ("260002", "260002_20261112T090001")
            assert post(r, html.replace("<h1>", "<!-- other bytes --><h1>"))[1] == found and len(case_folders(r)) == 2
            assert folder_bytes(r, first["case_id"]) == left and "replaced_by" not in capture_meta(r, first["case_id"])


def folder_bytes(r: Running, case_id: str) -> dict[str, bytes]:
    """Every file of the case folder, by its path under the folder, with its bytes."""
    root = r.settings.cases_root / case_id
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_grey_case_the_buyer_closed_is_left_as_it_is(capture_server, tmp_path, monkeypatch):
    """Q2 (d): a case without an order is replaced only while it is in review. One that the buyer closed answers while no order is
    found, and is left exactly as it is once the order is filed: no mark, not even for a moment, no second record, its supplier
    kept, every file byte for byte; the new case answers from then on. A case without an order that is in review is replaced
    whatever was decided in it: nothing was compared in it, and its files stay."""
    r = capture_server
    written, real = [], server_module._write_json
    monkeypatch.setattr(server_module, "_write_json", lambda path, data: written.append(path.parent.name) or real(path, data))
    html = cart_page()
    grey = post(r, html)[1]
    _decide_all(r, grey["case_id"], "keep")                                     # the buyer closed the case without an order
    assert result_of(r, grey["case_id"])["status"] == "closed" and open_cases(r) == []
    closed = post(r, html)[1]
    assert (closed["case_id"], closed["verdict"], closed["status"]) == (grey["case_id"], "no_po", "closed")
    before = folder_bytes(r, grey["case_id"])
    exported(r, tmp_path, "order.csv", {"250981": OWN})
    found = post(r, html)[1]
    assert (found["po_number"], found["verdict"]) == ("250981", "match") and found["case_id"] != grey["case_id"]
    assert folder_bytes(r, grey["case_id"]) == before and "replaced_by" not in capture_meta(r, grey["case_id"])
    assert result_of(r, grey["case_id"])["supplier"] == "S22" and [rec["action"] for rec in close_records(r, grey["case_id"])] == ["close"]
    assert post(r, html)[1] == found and post(r, cart_page(note="<!-- other bytes -->"))[1] == found
    assert folder_bytes(r, grey["case_id"]) == before and len(case_folders(r)) == 2
    assert written == [grey["case_id"], found["case_id"]]                       # each case's capture.json written once, at its opening
    # in review, with a decision and a close that closed nothing: replaced all the same
    items = ("KM-9003", "KM-9004")
    page_two = cart_page(items)
    grey_two = post(r, page_two)[1]
    case, [item] = _items(r, grey_two["case_id"])
    decide(case, item["item_id"], "unresolved")
    assert close_case(case, r.deps).status == "review" and open_cases(r) == [grey_two["case_id"]]
    exported(r, tmp_path, "second.csv", {"250982": items})
    found_two = post(r, page_two)[1]
    assert found_two["po_number"] == "250982" and capture_meta(r, grey_two["case_id"])["replaced_by"] == found_two["case_id"]
    assert result_of(r, grey_two["case_id"])["status"] == "closed" and open_cases(r) == []
    assert [rec["action"] for rec in close_records(r, grey_two["case_id"])] == ["close", "replaced"]
    assert (r.settings.cases_root / grey_two["case_id"] / "decisions.jsonl").is_file()


def test_a_grey_case_of_an_earlier_day_is_replaced_when_the_cart_finds_its_order(capture_server, tmp_path, monkeypatch):
    """Q2 (e), the review's finding 9a: when a new case with an order is opened for a cart, the cases without an order that hold the
    same cart and are still in review are finished as replaced, of any day, although the cart is looked for among the capture cases
    of the day only. One that the buyer closed, one whose result cannot be read, and a case without an order of another cart, are
    left as they are. They are looked for when a new case with an order is opened, not at every capture."""
    r = capture_server
    clock = [datetime(2026, 11, 9, 15, 0)]
    r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
    looked, real = [], server_module._same_lines

    def counted(*args):
        looked.append(len(looked) + 1)
        return real(*args)
    monkeypatch.setattr(server_module, "_same_lines", counted)
    torn = post(r, cart_page(note="<!-- day 0 -->"))[1]                         # a case of the same cart whose result cannot be read
    (r.settings.cases_root / torn["case_id"] / "steps" / "08_result.json").write_bytes(b'{"case_id": "unkn')
    clock[0] = datetime(2026, 11, 10, 15, 0)
    first = post(r, cart_page(note="<!-- day 1 -->"))[1]
    _decide_all(r, first["case_id"], "keep")                                    # the buyer closed the case of the first day
    clock[0] = datetime(2026, 11, 11, 15, 0)
    second = post(r, cart_page(note="<!-- day 2 -->"))[1]
    other = post(r, cart_page(("KM-9003", "KM-9004")))[1]                       # another cart without an order
    assert (first["case_id"], second["case_id"], other["case_id"]) == ("unknown_20261110T150000", "unknown_20261111T150000",
                                                                       "unknown_20261111T150001")
    assert open_cases(r) == [second["case_id"], other["case_id"]] and looked == []
    before = folder_bytes(r, first["case_id"]), folder_bytes(r, other["case_id"]), folder_bytes(r, torn["case_id"])
    exported(r, tmp_path, "order.csv", {"250981": OWN})                         # filed on the third day, at eight
    clock[0] = datetime(2026, 11, 12, 8, 5)
    status, found = post(r, cart_page(note="<!-- day 3 -->"))
    assert status == 200 and (found["po_number"], found["case_id"], found["verdict"]) == ("250981", "250981_20261112T080500", "match")
    assert looked == [1]
    assert capture_meta(r, second["case_id"])["replaced_by"] == found["case_id"] and result_of(r, second["case_id"])["status"] == "closed"
    assert [(rec["action"], rec["by"]) for rec in close_records(r, second["case_id"])] == [("replaced", found["case_id"])]
    assert (folder_bytes(r, first["case_id"]), folder_bytes(r, other["case_id"]), folder_bytes(r, torn["case_id"])) == before
    assert open_cases(r) == [other["case_id"]]
    assert post(r, cart_page(note="<!-- day 3 -->"))[1] == found and post(r, cart_page(note="<!-- again -->"))[1] == found
    assert looked == [1] and len(close_records(r, second["case_id"])) == 1


def test_a_decision_saved_before_the_mark_takes_the_mark_back(capture_server, tmp_path, monkeypatch):
    """Q2 (f): the replacement writes the mark and then reads the case again. A decision that the buyer saved on the guess right
    before the mark, after the pre-check last looked at the case, is found then: the mark is taken back, capture.json holds what it
    held, and the guess is left as it is, in review and on the review page, where its close carries the decision out."""
    r = capture_server
    released(r, tmp_path, "250986", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    held = capture_meta(r, guess["case_id"])
    old, [item] = _items(r, guess["case_id"])
    exported(r, tmp_path, "new.csv", {"250987": OWN})
    real, marks = server_module._write_json, []

    def decided_first(path, data):
        if path.parent.name == guess["case_id"] and "replaced_by" in data:
            marks.append(data["replaced_by"])
            decide(old, item["item_id"], "keep")                                # the buyer's decision lands right before the mark
        real(path, data)
    monkeypatch.setattr(server_module, "_write_json", decided_first)
    found = post(r, html)[1]
    monkeypatch.undo()
    assert (found["po_number"], found["case_id"]) == ("250987", "250987_20261112T090001") and marks == [found["case_id"]]
    assert capture_meta(r, guess["case_id"]) == held and close_records(r, guess["case_id"]) == []        # the mark was taken back
    assert result_of(r, guess["case_id"])["status"] == "review" and open_cases(r) == [guess["case_id"], found["case_id"]]
    assert post(r, html)[1] == found and capture_meta(r, guess["case_id"]) == held          # the case opened last answers
    assert close_case(old, r.deps).status == "closed"
    assert close_records(r, guess["case_id"])[0]["carried_out"][item["item_id"]]["decision"] == "keep"
    # a mark whose finish was cut off: the case is read again before it is finished, when a capture meets it and when the server
    # starts, and a decision that reached it in the instant of the mark takes the mark back then
    other = ("KM-9003", "KM-9004")
    released(r, tmp_path, "250988", other, at=EIGHT - DAY)
    guess_two = post(r, cart_page(other, prices={"KM-9004": "20,00"}))[1]
    held_two = capture_meta(r, guess_two["case_id"])
    exported(r, tmp_path, "newer.csv", {"250989": other})

    def ended(case, by, at, language):
        raise ProcessEnded("after the mark, before the finish")
    monkeypatch.setattr(server_module, "finish_replaced", ended)
    assert post(r, cart_page(other, prices={"KM-9004": "20,00"}, note="<!-- reload -->"))[0] == 500
    monkeypatch.undo()
    marked = capture_meta(r, guess_two["case_id"])
    assert marked == held_two | {"replaced_by": marked["replaced_by"]} and marked["replaced_by"].startswith("250989_")
    case_two, [item_two] = _items(r, guess_two["case_id"])
    saved = {"item_id": item_two["item_id"], "decision": "keep", "article_update": False, "at": "2026-11-12T09:00:01"}
    (case_two.root / "decisions.jsonl").write_text(json.dumps(saved) + "\n", encoding="utf-8")
    met = post(r, cart_page(other, prices={"KM-9004": "20,00"}, note="<!-- other bytes -->"))[1]         # a capture meets it
    assert met["case_id"] == marked["replaced_by"] and capture_meta(r, guess_two["case_id"]) == held_two
    assert result_of(r, guess_two["case_id"])["status"] == "review" and close_records(r, guess_two["case_id"]) == []
    assert guess_two["case_id"] in open_cases(r)
    (case_two.root / "capture.json").write_text(json.dumps(marked), encoding="utf-8")                    # the same state again
    serve(r.settings, r.deps, "127.0.0.1", 0, False).server_close()                                      # and the server starts
    assert capture_meta(r, guess_two["case_id"]) == held_two and result_of(r, guess_two["case_id"])["status"] == "review"
    assert close_records(r, guess_two["case_id"]) == [] and guess_two["case_id"] in open_cases(r)
    # the same state once more, now with the case that replaced it gone and that case's order out of the store: the capture that
    # meets the guess takes the mark back and is answered by the guess itself, whose order is the guess again (what _was_replaced
    # returns after the mark is taken back: the check's mutant N14)
    (case_two.root / "capture.json").write_text(json.dumps(marked), encoding="utf-8")
    shutil.rmtree(r.settings.cases_root / marked["replaced_by"])
    shutil.rmtree(r.deps.store.root / "250989")
    folders = case_folders(r)
    met_again = post(r, cart_page(other, prices={"KM-9004": "20,00"}, note="<!-- third bytes -->"))[1]
    assert (met_again["case_id"], met_again["po_number"]) == (guess_two["case_id"], "250988") and case_folders(r) == folders
    assert capture_meta(r, guess_two["case_id"]) == held_two and texts_of(met_again)[0] == SENT_NL.format(po="250988")


def _reported(r: Running, listed: list[str]) -> list[str]:
    """The cases the runner's next digest reports, of those `listed` for it and the capture cases not yet reported."""
    state = {"last_sent": None, "reported": list(listed), "skipped": []}
    runner._report_captures(r.settings.cases_root, state, r.deps.clock())
    return state["reported"]


def test_a_number_typed_into_the_cart_leaves_no_stale_case(capture_server, tmp_path):
    """Ruling Q13: capture.json keeps the lines digest, the cart without the number its page states. The buyer followed the bar of
    a guess and typed the order's number in the cart's reference field: that page is another cart, found by its number, and its
    new case replaces the guess of the same lines, which is off the review page and out of the digest. The same for a case without
    a PO. A guess the buyer decided on stays exactly as it is. A capture.json of before this round holds no lines digest and is
    matched by its cart digest alone, as before, so the case of such a cart with another number is not found."""
    r = capture_server
    root = r.settings.cases_root
    exported(r, tmp_path, "earlier.csv", {"260001": OWN}, at=EIGHT - DAY)
    guess = post(r, cart_page())[1]
    assert texts_of(guess)[0] == NOT_SURE_NL.format(po="260001") and open_cases(r) == [guess["case_id"]]
    found = post(r, cart_page(reference="260001"))[1]                           # the number typed into the cart
    assert (found["po_number"], found["case_id"], found["verdict"]) == ("260001", "260001_20261112T090001", "match")
    assert CHOSEN_NL not in found["message"] and texts_of(found) == []
    old, new = capture_meta(r, guess["case_id"]), capture_meta(r, found["case_id"])
    assert old["lines_digest"] == new["lines_digest"] and old["cart_digest"] != new["cart_digest"] and len(new["lines_digest"]) == 64
    assert old["replaced_by"] == found["case_id"] and result_of(r, guess["case_id"])["status"] == "closed"
    assert [(record["action"], record["by"]) for record in close_records(r, guess["case_id"])] == [("replaced", found["case_id"])]
    assert open_cases(r) == [] and _reported(r, [guess["case_id"]]) == [found["case_id"]]          # off the page, out of the digest
    # a case without a PO: the export is given and the number typed
    pair = ("KM-9003", "KM-9004")
    grey = post(r, cart_page(pair))[1]
    assert grey["verdict"] == "no_po" and open_cases(r) == [grey["case_id"]]
    exported(r, tmp_path, "pair.csv", {"260002": pair}, at=EIGHT - DAY)
    typed = post(r, cart_page(pair, reference="260002"))[1]
    assert (typed["po_number"], typed["verdict"]) == ("260002", "match")
    assert capture_meta(r, grey["case_id"])["replaced_by"] == typed["case_id"] and result_of(r, grey["case_id"])["status"] == "closed"
    assert open_cases(r) == [] and _reported(r, [grey["case_id"]]) == [found["case_id"], typed["case_id"]]
    # a guess the buyer decided on stays exactly as it is
    duo = ("KM-9005", "KM-9006")
    exported(r, tmp_path, "duo.csv", {"260003": duo}, at=EIGHT - DAY)
    decided = post(r, cart_page(duo, prices={"KM-9005": "8,05"}))[1]
    case, items = _items(r, decided["case_id"])
    decide(case, items[0]["item_id"], "unresolved")
    left = folder_bytes(r, decided["case_id"])
    beside = post(r, cart_page(duo, reference="260003", prices={"KM-9005": "8,05"}))[1]
    assert (beside["po_number"], beside["case_id"]) == ("260003", "260003_20261112T090001")
    assert folder_bytes(r, decided["case_id"]) == left and "replaced_by" not in capture_meta(r, decided["case_id"])
    assert decided["case_id"] in open_cases(r)
    # a capture.json of before this round: no lines digest, matched by its cart digest alone
    single = ("KM-9007",)
    exported(r, tmp_path, "single.csv", {"260004": single}, at=EIGHT - DAY)
    before = post(r, cart_page(single))[1]
    meta = capture_meta(r, before["case_id"])
    assert texts_of(before)[0] == NOT_SURE_NL.format(po="260004") and "lines_digest" in meta
    (root / before["case_id"] / "capture.json").write_text(
        json.dumps({name: value for name, value in meta.items() if name != "lines_digest"}), encoding="utf-8")
    later = post(r, cart_page(single, reference="260004"))[1]
    assert later["po_number"] == "260004" and "replaced_by" not in capture_meta(r, before["case_id"])
    assert before["case_id"] in open_cases(r) and result_of(r, before["case_id"])["status"] == "review"


def test_a_failed_case_of_the_same_lines_is_never_replaced(capture_server, tmp_path):
    """Ruling Q13 beside the brief's rule that a failed case is never reused: the cases of the same lines that a new case replaces
    are cases that could answer. A guess whose run failed, and a case without a PO whose run failed, are left exactly as they are
    when the cart with its number typed opens its case: their failed result stays, to be looked at."""
    r = capture_server
    exported(r, tmp_path, "earlier.csv", {"260001": OWN}, at=EIGHT - DAY)
    healthy = r.server.deps

    def failing(path, kind):
        raise ReaderError("made-up reader failure")
    r.server.deps = healthy.model_copy(update={"reader": failing})
    guess = post(r, cart_page())[1]                                             # a guess whose run fails
    grey = post(r, cart_page(("KM-9003", "KM-9004")))[1]                        # and a cart without an order whose run fails
    r.server.deps = healthy
    assert (guess["verdict"], guess["po_number"], grey["verdict"], grey["po_number"]) == ("failed", "260001", "failed", None)
    left = folder_bytes(r, guess["case_id"]), folder_bytes(r, grey["case_id"])
    exported(r, tmp_path, "pair.csv", {"260002": ("KM-9003", "KM-9004")}, at=EIGHT - DAY)
    typed = post(r, cart_page(reference="260001"))[1], post(r, cart_page(("KM-9003", "KM-9004"), reference="260002"))[1]
    assert [answer["verdict"] for answer in typed] == ["match", "match"] and len(case_folders(r)) == 4
    assert (folder_bytes(r, guess["case_id"]), folder_bytes(r, grey["case_id"])) == left
    assert result_of(r, guess["case_id"])["status"] == "failed" and result_of(r, grey["case_id"])["status"] == "failed"
    exported(r, tmp_path, "duo.csv", {"260003": ("KM-9005", "KM-9006")}, at=EIGHT - DAY)
    no_result = post(r, cart_page(("KM-9005", "KM-9006")))[1]                   # a guess whose run was cut off: it has no result
    assert texts_of(no_result)[0] == NOT_SURE_NL.format(po="260003")
    (r.settings.cases_root / no_result["case_id"] / "steps" / "08_result.json").unlink()
    status, numbered = post(r, cart_page(("KM-9005", "KM-9006"), reference="260003"))
    assert status == 200 and (numbered["po_number"], numbered["verdict"]) == ("260003", "match")
    assert "replaced_by" not in capture_meta(r, no_result["case_id"])
    assert not (r.settings.cases_root / no_result["case_id"] / "steps" / "08_result.json").exists()


SHOP_A, SHOP_B = "http://shop-a.example.com/cart", "http://shop-b.example.org/cart"


def test_the_cases_of_the_same_lines_are_matched_within_one_webshop(tmp_path, madeup_out):
    """The clean-up's ruling D1 on what "one cart" is in Q13: a new case with a PO replaces a case of the same lines digest only
    when that case's capture.json holds the host of the new case's page. The guess of a cart at one webshop stays when a cart of
    the same lines at another webshop, which states its number, opens its case (the check's d2_basis: the two carts differ in the
    price basis alone, which the digest leaves out); the cart with its number at the first webshop does replace it. A
    capture.json without a host is matched as before. An empty cart table has no line, so it is never matched by its lines
    digest: the case of an empty table stays, at another webshop and at its own."""
    clock = [NINE]
    with running(tmp_path, madeup_out) as r:
        r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
        root = r.settings.cases_root
        exported(r, tmp_path, "two.csv", {"260001": OWN, "260002": OWN})                    # two orders of today with the cart's items: a guess
        per_one, per_hundred = cart_page(), cart_page(reference="260001").replace("<td>1</td></tr>", "<td>100</td></tr>", 1)
        guess = post(r, per_one, url=SHOP_A)[1]
        assert (guess["po_number"], texts_of(guess)[0]) == ("260002", NOT_SURE_NL.format(po="260002"))
        assert capture_meta(r, guess["case_id"])["host"] == "shop-a.example.com"
        clock[0] = NINE + timedelta(minutes=2)
        other = post(r, per_hundred, url=SHOP_B)[1]                                         # the same four values per line, at another webshop
        assert (other["po_number"], other["case_id"]) == ("260001", "260001_20261112T090200")
        assert capture_meta(r, other["case_id"])["lines_digest"] == capture_meta(r, guess["case_id"])["lines_digest"]
        assert "replaced_by" not in capture_meta(r, guess["case_id"]) and result_of(r, guess["case_id"])["status"] == "review"
        assert guess["case_id"] in open_cases(r) and close_records(r, guess["case_id"]) == []
        # a capture.json without a host: matched as before, by its lines digest alone
        old = post(r, cart_page(("KM-9003", "KM-9004")), url=SHOP_A)[1]
        meta = capture_meta(r, old["case_id"])
        (root / old["case_id"] / "capture.json").write_text(json.dumps({k: v for k, v in meta.items() if k != "host"}), encoding="utf-8")
        exported(r, tmp_path, "pair.csv", {"260003": ("KM-9003", "KM-9004")}, at=EIGHT - DAY)
        typed = post(r, cart_page(("KM-9003", "KM-9004"), reference="260003"), url=SHOP_B)[1]
        assert typed["po_number"] == "260003" and capture_meta(r, old["case_id"])["replaced_by"] == typed["case_id"]
        # the cart with its number at the first webshop: the guess of that webshop is replaced
        clock[0] = NINE + timedelta(minutes=4)
        same_shop = post(r, cart_page(reference="260002"), url=SHOP_A)[1]
        assert same_shop["po_number"] == "260002" and capture_meta(r, guess["case_id"])["replaced_by"] == same_shop["case_id"]
        # two empty cart tables: the case of an empty table is never matched by its lines digest
        empty = cart_page(())
        assert read(tmp_path, empty).lines == []
        nothing = post(r, empty, url=SHOP_A)[1]
        assert nothing["verdict"] == "no_po" and nothing["case_id"] in open_cases(r)
        for days, url in ((1, SHOP_A), (2, SHOP_B)):        # an empty table that states a known order's number, each on a day of its own
            clock[0] = NINE + timedelta(days=days)          # (on one day the second would be answered by the first as the same cart)
            stated = post(r, cart_page((), reference="260001", note=f"<!-- {url} -->"), url=url)[1]
            assert (stated["po_number"], stated["case_id"][-15:-6]) == ("260001", f"202611{12 + days}T"), url
            assert capture_meta(r, stated["case_id"])["lines_digest"] == capture_meta(r, nothing["case_id"])["lines_digest"]
            assert capture_meta(r, stated["case_id"])["host"] == capture_meta(r, nothing["case_id"])["host"] or url == SHOP_B
            assert "replaced_by" not in capture_meta(r, nothing["case_id"]) and result_of(r, nothing["case_id"])["status"] == "review"


def test_a_case_of_the_same_lines_with_a_later_id_is_left_alone(capture_server, tmp_path):
    """Q13 (b), the check's mutant A5: the cases of the same lines that a new case replaces are those not opened after it, by the
    times in their ids. With a clock that stands still an untouched guess of the same lines can hold an id one second later than
    the new case (its page was checked a second time after a run that failed): that guess is left alone."""
    r = capture_server
    exported(r, tmp_path, "earlier.csv", {"260001": OWN, "260002": ("KM-9003", "KM-9004")}, at=EIGHT - DAY)
    healthy = r.server.deps

    def failing(path, kind):
        raise ReaderError("made-up reader failure")
    r.server.deps = healthy.model_copy(update={"reader": failing})
    failed = post(r, cart_page())[1]
    r.server.deps = healthy
    later = post(r, cart_page())[1]                                             # the same page after the failed run: a second later
    assert (failed["verdict"], failed["case_id"], later["case_id"]) == ("failed", "260001_20261112T090000", "260001_20261112T090001")
    assert texts_of(later)[0] == NOT_SURE_NL.format(po="260001")
    left = folder_bytes(r, later["case_id"])
    typed = post(r, cart_page(reference="260002"))[1]                           # the same lines with another order's number: opened at 9:00:00
    assert (typed["po_number"], typed["case_id"]) == ("260002", "260002_20261112T090000")
    assert folder_bytes(r, later["case_id"]) == left and "replaced_by" not in capture_meta(r, later["case_id"])
    assert later["case_id"] in open_cases(r)


def test_a_replaced_case_whose_result_says_review_is_finished_again(capture_server, tmp_path):
    """Q11 (b), the check's mutant A17: a marked case that holds the record of its replacement is finished only when its result
    is closed as well. When a close of the review page wrote its stale result between the finish's two writes, the result says
    review while the replacement is the last record: the next capture that meets the case, and the server's start, close it
    again."""
    r = capture_server
    html = cart_page()
    grey = post(r, html)[1]
    exported(r, tmp_path, "order.csv", {"250998": OWN})
    found = post(r, html)[1]
    case = load_case(r.settings.cases_root, grey["case_id"])
    assert capture_meta(r, grey["case_id"])["replaced_by"] == found["case_id"] and result_of(r, grey["case_id"])["status"] == "closed"

    def stale():
        case.write_step(8, "result", result_of(r, grey["case_id"]) | {"status": "review"})
        assert [record["action"] for record in close_records(r, grey["case_id"])][-1] == "replaced"
    stale()
    assert post(r, html.replace("<h1>", "<!-- other bytes --><h1>"))[1] == found          # a capture meets it
    assert result_of(r, grey["case_id"])["status"] == "closed" and open_cases(r) == []
    assert [(record["action"], record["by"]) for record in close_records(r, grey["case_id"])] == [("replaced", found["case_id"])] * 2
    stale()
    serve(r.settings, r.deps, "127.0.0.1", 0, False).server_close()                        # and the server's start
    assert result_of(r, grey["case_id"])["status"] == "closed"
    assert [record["action"] for record in close_records(r, grey["case_id"])] == ["replaced"] * 3


def test_a_refused_decision_takes_only_its_own_line_out(capture_server, tmp_path, monkeypatch):
    """The clean-up's ruling D3: a decision that is refused after its append, because the case was marked meanwhile, takes its own
    line out of decisions.jsonl and no other. A line that another decision appended in the same instant, after this decision
    measured the file and before it appended, stays: the file's tail is compared with the line that was appended."""
    r = capture_server
    html = cart_page()
    grey = post(r, html)[1]
    case, [item] = _items(r, grey["case_id"])
    exported(r, tmp_path, "order.csv", {"250998": OWN})
    append = app_module._append_line
    path = case.root / "decisions.jsonl"

    def with_another_in_between(target, line):                                  # between this decision's measuring and its append
        monkeypatch.setattr(app_module, "_append_line", append)
        decide(case, item["item_id"], "keep")                                   # another decision, taken
        assert post(r, html)[0] == 200                                          # and the pre-check's mark
        append(target, line)
    monkeypatch.setattr(app_module, "_append_line", with_another_in_between)
    with pytest.raises(ValueError, match="was replaced by another case, so no decision is taken on it"):
        decide(case, item["item_id"], "unresolved")
    [kept] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (kept["item_id"], kept["decision"]) == (item["item_id"], "keep") and path.read_bytes().endswith(b"}\n")
    assert capture_meta(r, grey["case_id"])["replaced_by"].startswith("250998_")
    # a mark that falls after the first look, on a file with a line in it and on a file cut off without a newline: the other
    # line stays in both, and on the second the newline that the append put before its own line
    held = path.read_bytes()
    for before in (held, held.rstrip(b"\n")):
        path.write_bytes(before)
        monkeypatch.setattr(app_module, "marked_replaced", lambda root, looks=[]: looks.append(1) or len(looks) > 1)
        with pytest.raises(ValueError, match="was replaced by another case"):
            decide(case, item["item_id"], "confirm")
        monkeypatch.undo()
        assert path.read_bytes() == held
    # a line that another decision appended behind this one's: nothing is cut off

    def with_another_behind(target, line):
        append(target, line)
        append(target, '{"item_id": "RI9", "decision": "keep"}')
    monkeypatch.setattr(app_module, "_append_line", with_another_behind)
    monkeypatch.setattr(app_module, "marked_replaced", lambda root, looks=[]: looks.append(1) or len(looks) > 1)
    with pytest.raises(ValueError, match="was replaced by another case"):
        decide(case, item["item_id"], "confirm")
    monkeypatch.undo()
    assert path.read_bytes().startswith(held) and path.read_bytes().endswith(b'{"item_id": "RI9", "decision": "keep"}\n')
    assert len(path.read_bytes().splitlines()) == 3


def test_yesterdays_untouched_guess_is_replaced_when_the_same_cart_opens_a_case_today(tmp_path, madeup_out):
    """Ruling Q13 (b): when a new case with a PO is opened, every capture case of any day that holds the same lines and may still
    be replaced goes with the earlier cases of the page and the cart: yesterday's guess on which the buyer did nothing is finished
    as replaced when the same cart opens a case today, a case of an order found for sure and a case that is a guess again. A guess
    of yesterday that the buyer acted on stays, and so does a case of other lines (the same item at another price). A capture.json
    of before this round, without a lines digest, is matched by its cart digest, so yesterday's untouched guess of the same cart is
    replaced as well. The page of yesterday's guess itself is answered by its own case while its order is still the guess: nothing
    else changes in which case answers."""
    clock = [datetime(2026, 11, 11, 15, 0)]
    with running(tmp_path, madeup_out) as r:
        r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
        root = r.settings.cases_root
        pair, duo, single = ("KM-9003", "KM-9004"), ("KM-9005", "KM-9006"), ("KM-9007",)
        exported(r, tmp_path, "old.csv", {"260001": OWN, "260002": pair, "260003": duo, "260004": single}, at=EIGHT - 3 * DAY)
        pages = {codes: cart_page(codes, note="<!-- yesterday -->") for codes in (OWN, pair, duo, single)}
        pages["other lines"] = cart_page(single, prices={"KM-9007": "98,00"}, note="<!-- yesterday -->")
        yesterday = {codes: post(r, html)[1] for codes, html in pages.items()}
        assert all(texts_of(answer)[0].startswith("De assistent weet niet zeker") for answer in yesterday.values())
        case, items = _items(r, yesterday[duo]["case_id"])
        decide(case, items[0]["item_id"], "unresolved")                         # the buyer acted on the guess of the duo
        old = root / yesterday[single]["case_id"] / "capture.json"              # the guess of the single item is of before this round
        old.write_text(json.dumps({name: value for name, value in json.loads(old.read_text(encoding="utf-8")).items()
                                   if name != "lines_digest"}), encoding="utf-8")
        left = folder_bytes(r, yesterday[duo]["case_id"]), folder_bytes(r, yesterday["other lines"]["case_id"])
        clock[0] = NINE                                                         # the next day
        exported(r, tmp_path, "own.csv", {"260011": OWN})                       # a new order of today with exactly the first cart's items
        today = {codes: post(r, cart_page(codes, note="<!-- today -->"))[1] for codes in (OWN, pair, duo, single)}
        assert (today[OWN]["po_number"], today[OWN]["case_id"]) == ("260011", "260011_20261112T090000") and CHOSEN_NL in today[OWN]["message"]
        assert (today[pair]["po_number"], today[pair]["case_id"]) == ("260002", "260002_20261112T090000")      # a guess again
        assert texts_of(today[pair])[0] == NOT_SURE_NL.format(po="260002")
        for codes in (OWN, pair, single):                                       # found, a guess, and a capture.json without a lines digest
            assert capture_meta(r, yesterday[codes]["case_id"])["replaced_by"] == today[codes]["case_id"], codes
            assert result_of(r, yesterday[codes]["case_id"])["status"] == "closed", codes
        assert today[duo]["case_id"] == "260003_20261112T090000" and folder_bytes(r, yesterday[duo]["case_id"]) == left[0]
        assert folder_bytes(r, yesterday["other lines"]["case_id"]) == left[1]  # other lines: left as it is
        assert sorted(open_cases(r)) == sorted([yesterday[duo]["case_id"], yesterday["other lines"]["case_id"], today[pair]["case_id"],
                                                today[duo]["case_id"], today[single]["case_id"]])
        own_page = post(r, pages["other lines"])[1]                             # yesterday's page itself: its own case answers
        assert own_page["case_id"] == yesterday["other lines"]["case_id"] and len(case_folders(r)) == 9


def test_a_try_out_cart_and_a_webshops_cart_of_the_same_lines_are_never_mixed(tmp_path, madeup_out):
    """Ruling Q13 (a): the lines digest of a try-out cart holds the try-out's mark, as its cart digest does. So the case of a
    try-out cart never replaces the guess of a webshop's cart with the same lines, and the other way round."""
    with running(tmp_path, madeup_out, settings=OPEN) as r:
        filed(r, "250201", -DAY)                                                # the order of the made-up set, filed yesterday
        po = r.deps.store.as_it_stands("250201")
        tryout = cart_html(po, "none", TODAY)
        real = tryout.replace("Proefwinkel (verzonnen pagina)", "S21").replace("Uw referentie: 250201", "Uw referentie: volgt")
        cart = read(tmp_path, real)
        assert cart.po_reference.value is None and "Uw referentie: volgt" in real
        assert server_module.lines_digest(cart) == server_module.lines_digest(read(tmp_path, tryout))
        assert server_module.lines_digest(cart) != server_module.lines_digest(cart, tryout=True)
        # a try-out cart that found no order (its order was not in the store), then the webshop's cart of the same lines with its number
        hidden = tmp_path / "hidden"
        shutil.move(r.deps.store.root / "250201", hidden)
        grey = post(r, tryout)[1]
        assert grey["verdict"] == "no_po" and tryout_case(load_case(r.settings.cases_root, grey["case_id"]))
        shutil.move(hidden, r.deps.store.root / "250201")
        numbered = post(r, real.replace("Uw referentie: volgt", "Uw referentie: 250201"))[1]
        assert numbered["po_number"] == "250201" and not tryout_case(load_case(r.settings.cases_root, numbered["case_id"]))
        assert "replaced_by" not in capture_meta(r, grey["case_id"]) and result_of(r, grey["case_id"])["status"] == "review"
        # the other way round: the guess of the webshop's cart without its number, then the try-out cart of the same lines
        guess = post(r, real)[1]
        assert texts_of(guess)[0] == NOT_SURE_NL.format(po="250201") and not tryout_case(load_case(r.settings.cases_root, guess["case_id"]))
        tried = post(r, tryout)[1]                                              # found by its number now: a new case, a try-out's
        assert (tried["po_number"], tried["verdict"]) == ("250201", "match") and tryout_case(load_case(r.settings.cases_root, tried["case_id"]))
        assert capture_meta(r, grey["case_id"])["replaced_by"] == tried["case_id"]             # the try-out's own case without a PO goes
        assert "replaced_by" not in capture_meta(r, guess["case_id"]) and open_cases(r) == [guess["case_id"]]
        assert capture_meta(r, guess["case_id"])["lines_digest"] != capture_meta(r, tried["case_id"])["lines_digest"]
        assert capture_meta(r, guess["case_id"])["lines_digest"] == capture_meta(r, numbered["case_id"])["lines_digest"]


def test_a_cart_without_an_order_is_replaced_by_a_guess_too(tmp_path, madeup_out):
    """Q2 (c) and (e), the check's finding 5 (its mutants W5 and W1): under ruling Q1 the order that comes for a cart without one
    is usually a guess, an order exported on an earlier day or with an item more than the cart. On the same page that guess opens
    a new case, and the case without a PO is finished as replaced; on the next day, in other bytes, the guess of that day replaces
    the case without a PO of the day before as well."""
    clock = [datetime(2026, 11, 11, 15, 0)]
    with running(tmp_path, madeup_out) as r:
        r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
        pair = ("KM-9003", "KM-9004")
        html, html_pair = cart_page(), cart_page(pair, note="<!-- day 1 -->")
        grey, grey_pair = post(r, html)[1], post(r, html_pair)[1]
        assert (grey["verdict"], grey_pair["verdict"]) == ("no_po", "no_po") and len(open_cases(r)) == 2
        # the same page, the same day: the order comes with one item more than the cart, so it is a guess
        exported(r, tmp_path, "more.csv", {"250981": (*OWN, "KM-9005")}, at=datetime(2026, 11, 11, 14, 0).timestamp())
        assert order_of(r, tmp_path, html, now=clock[0]) == ("250981", NOT_SURE)
        guess = post(r, html)[1]
        assert (guess["po_number"], guess["case_id"]) == ("250981", "250981_20261111T150001")
        assert texts_of(guess)[0] == NOT_SURE_NL.format(po="250981")
        assert capture_meta(r, grey["case_id"])["replaced_by"] == guess["case_id"] and result_of(r, grey["case_id"])["status"] == "closed"
        assert [(record["action"], record["by"]) for record in close_records(r, grey["case_id"])] == [("replaced", guess["case_id"])]
        assert open_cases(r) == [guess["case_id"], grey_pair["case_id"]]
        # the next day, in other bytes: the order of the second cart was exported on an earlier day, so it is a guess
        exported(r, tmp_path, "pair.csv", {"250982": pair}, at=datetime(2026, 11, 11, 16, 0).timestamp())
        clock[0] = NINE
        assert order_of(r, tmp_path, html_pair) == ("250982", NOT_SURE)
        next_day = post(r, cart_page(pair, note="<!-- day 2 -->"))[1]
        assert (next_day["po_number"], next_day["case_id"]) == ("250982", "250982_20261112T090000")
        assert texts_of(next_day)[0] == NOT_SURE_NL.format(po="250982")
        assert capture_meta(r, grey_pair["case_id"])["replaced_by"] == next_day["case_id"]
        assert result_of(r, grey_pair["case_id"])["status"] == "closed" and open_cases(r) == [guess["case_id"], next_day["case_id"]]
        assert prepared_rows(r) == []


def test_a_marked_case_is_off_the_review_page_and_refuses_a_decision_a_close_and_an_undo(capture_server, tmp_path, monkeypatch):
    """Q2 (f), the review's finding 6 (its s_late_decision and s_cut_same_bytes): a replacement cut off between the mark and the
    finish leaves the earlier case marked while its result is still in review. From the mark on the review page does not offer
    it, and a decision, a close and an undo on it are refused, so that no decision is saved on it that would be lost at the
    finish. The page of the capture that was cut off does not finish the case: the new case holds those bytes and answers at once.
    The same cart in a page of other bytes does."""
    r = capture_server
    root = r.settings.cases_root

    def ended(case, by, at, language):
        raise ProcessEnded("after the mark, before the finish")
    released(r, tmp_path, "260001", OWN, at=EIGHT - DAY)                        # only an order that was sent: a guess
    guess = post(r, cart_page(prices={"KM-9002": "44,00"}, note="<!-- t1 -->"))[1]
    assert texts_of(guess)[0] == SENT_NL.format(po="260001") and open_cases(r) == [guess["case_id"]]
    exported(r, tmp_path, "own.csv", {"260002": OWN})                           # the export of the cart's own order
    reload = cart_page(prices={"KM-9002": "44,00"}, note="<!-- t2 -->")         # the reload: the same cart, other bytes
    monkeypatch.setattr(server_module, "finish_replaced", ended)
    assert post(r, reload)[0] == 500                                            # the process ended between the mark and the finish
    monkeypatch.undo()
    [new] = [name for name in case_folders(r) if name.startswith("260002_")]
    case, [item] = _items(r, guess["case_id"])
    assert capture_meta(r, guess["case_id"])["replaced_by"] == new and result_of(r, guess["case_id"])["status"] == "review"
    assert close_records(r, guess["case_id"]) == [] and result_of(r, new)["status"] == "review"
    # from the mark on: not offered, and a decision, a close and an undo are refused
    assert open_cases(r) == [new] and [summary.case_id for summary in offered_cases(root)] == [new]
    assert selection_from_query(root, {"case": guess["case_id"]}) == (None, None)
    appended = []
    monkeypatch.setattr(app_module, "_append_line", lambda path, line: appended.append(path))
    with pytest.raises(ValueError, match="replaced"):
        decide(case, item["item_id"], "confirm")
    monkeypatch.undo()
    assert appended == []                                                       # refused at the first look: nothing is appended, not for a moment
    assert decision_text(case, item["item_id"], "confirm", False, "nl") == (STRINGS["nl"]["refused"], False)
    assert not (case.root / "decisions.jsonl").exists()
    with pytest.raises(ValueError, match="is not in review"):
        close_case(case, r.deps)
    assert close_text(case, r.deps, "nl") == (STRINGS["nl"]["not_closed"], False) and close_records(r, guess["case_id"]) == []
    # the page of the capture that was cut off: the new case answers by its bytes, and the marked case is not met
    for _ in range(2):
        again = post(r, reload)[1]
        assert again["case_id"] == new and result_of(r, guess["case_id"])["status"] == "review" and open_cases(r) == [new]
    # the same cart in a page of other bytes finishes it
    third = post(r, cart_page(prices={"KM-9002": "44,00"}, note="<!-- t3 -->"))[1]
    assert third["case_id"] == new and result_of(r, guess["case_id"])["status"] == "closed"
    assert [(rec["action"], rec["by"]) for rec in close_records(r, guess["case_id"])] == [("replaced", new)]
    assert not (case.root / "decisions.jsonl").exists() and open_cases(r) == [new] and len(case_folders(r)) == 2
    # an undo on a marked case is refused before anything is asked of the writer
    exported(r, tmp_path, "third.csv", {"260003": ("KM-9003", "KM-9004")})
    written = post(r, cart_page(("KM-9003", "KM-9004"), prices={"KM-9004": "18,10"}))[1]       # 0.6 percent: R04 prepares
    assert written["verdict"] == "match"
    holder = load_case(root, written["case_id"])
    log = holder.root / "write_log.jsonl"
    entry_id = json.loads(log.read_text(encoding="utf-8").splitlines()[0])["entry_id"]
    meta = capture_meta(r, written["case_id"])
    (holder.root / "capture.json").write_text(json.dumps(meta | {"replaced_by": "260003_20261112T100000"}), encoding="utf-8")
    lines = log.read_text(encoding="utf-8")
    assert undo(holder, entry_id, r.deps) is None and log.read_text(encoding="utf-8") == lines and close_records(r, written["case_id"]) == []
    (holder.root / "capture.json").write_text(json.dumps(meta), encoding="utf-8")
    assert undo(holder, entry_id, r.deps) is not None and len(log.read_text(encoding="utf-8").splitlines()) == 3


def test_the_reviews_lost_decision_is_kept_now(tmp_path, madeup_out):
    """The review's failing sequence of its finding 6 (seed 222 of its search, minimised), with what it gives now. There, the cart's
    own order got its PDF, another order without one was found in its place, the guess was marked and its finish cut off, and a
    decision saved on it meanwhile was lost. Now the guess's order is still the order the cart is compared with, as a guess, so
    the guess answers, nothing is marked, and the decision is carried out at the close."""
    clock = [datetime(2026, 11, 13, 8, 34, 35)]
    with running(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"language": "en"})) as r:
        r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
        four = ("KM-9001", "KM-9002", "KM-9004", "KM-9003")
        exported(r, tmp_path, "two.csv", {"260007": four, "260010": ("KM-9001", "KM-9002", "KM-9007")}, suppliers={"260007": "S21"},
                 at=datetime(2026, 11, 12, 9, 3, 16).timestamp())
        html = cart_page(four, supplier="S21", prices={"KM-9003": "0,22"})
        guess = post(r, html)[1]
        assert (guess["po_number"], guess["case_id"]) == ("260007", "260007_20261113T083435")
        assert texts_of(guess)[0] == NOT_SURE_EN.format(po="260007")
        released(r, tmp_path, "260007", four, supplier="S21", at=datetime(2026, 11, 13, 8, 34, 37).timestamp())
        clock[0] = datetime(2026, 11, 13, 8, 45, 45)
        assert order_of(r, tmp_path, html, now=clock[0]) == ("260007", ALREADY_SENT)
        again = post(r, html)[1]                                                 # the same case, with the line of the kind found now (Q12)
        assert (again["case_id"], texts_of(again)[0]) == (guess["case_id"], SENT_EN.format(po="260007"))
        assert case_folders(r) == [guess["case_id"]] and "replaced_by" not in capture_meta(r, guess["case_id"])
        case, [item] = _items(r, guess["case_id"])
        decide(case, item["item_id"], "keep")
        clock[0] = datetime(2026, 11, 13, 8, 55, 50)
        serve(r.settings, r.server.deps, "127.0.0.1", 0, False).server_close()   # the server starts: nothing is marked, nothing to finish
        assert open_cases(r) == [guess["case_id"]] and result_of(r, guess["case_id"])["status"] == "review"
        assert close_case(case, r.server.deps).status == "closed"
        [record] = close_records(r, guess["case_id"])
        assert (record["action"], list(record["carried_out"])) == ("close", [item["item_id"]])


def test_a_standing_case_that_holds_the_page_answers_before_any_later_case_of_the_cart(tmp_path, madeup_out):
    """Q2 (h), the review's finding 20: of the earlier cases the one opened last decides, with one exception. A standing case that
    holds the page itself answers at once: the cart is not read, and a later case of the same cart of that day is not looked at.
    So the page of the first day is answered by the case of the first day, and the same cart in the bytes of the second day by the
    case of the second day."""
    clock = [NINE]
    with running(tmp_path, madeup_out) as r:
        r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
        exported(r, tmp_path, "first.csv", {"260001": OWN})
        page_one, page_two = cart_page(note="<!-- a -->"), cart_page(note="<!-- b -->")
        first = post(r, page_one)[1]
        assert (first["case_id"], first["message"]) == ("260001_20261112T090000", f"Winkelwagen komt overeen met inkooporder 260001{CHOSEN_NL}")
        released(r, tmp_path, "260001", OWN, at=EIGHT + 2 * 3600)               # the order is released
        exported(r, tmp_path, "next.csv", {"260002": (*OWN, "KM-9003")}, at=EIGHT + 3 * 3600)      # the next order of the shop
        clock[0] = datetime(2026, 11, 13, 11, 10)
        second = post(r, page_two)[1]                   # the same cart in other bytes, the next day: a case of that day
        assert (second["case_id"], second["po_number"], texts_of(second)) == ("260001_20261113T111000", "260001", [SENT_NL.format(po="260001")])
        for minute in (11, 12):
            clock[0] = datetime(2026, 11, 13, 11, minute)
            assert post(r, page_one)[1] == first and post(r, page_two)[1] == second
        assert case_folders(r) == [first["case_id"], second["case_id"]]


def test_a_guess_answers_while_no_order_is_found_now(capture_server, tmp_path):
    """Q2 (c): a guess on which nothing was done keeps answering while no order at all is found now, as when its order was changed
    and holds none of the cart's items any more. No case without an order is opened in its place."""
    r = capture_server
    exported(r, tmp_path, "old.csv", {"250951": (*OWN, "KM-9003")}, at=EIGHT - DAY)
    html = cart_page()
    guess = post(r, html)[1]
    assert (guess["po_number"], texts_of(guess)[0]) == ("250951", NOT_SURE_NL.format(po="250951"))
    exported(r, tmp_path, "changed.csv", {"250951": ("KM-9004", "KM-9005")})    # the order as it stands holds other items now
    assert order_of(r, tmp_path, html) == (None, None)
    assert post(r, html)[1] == guess and post(r, cart_page(note="<!-- other bytes -->"))[1] == guess
    assert case_folders(r) == [guess["case_id"]] and "replaced_by" not in capture_meta(r, guess["case_id"]) and len(captures(r)) == 1


def test_a_case_without_an_order_replaces_nothing(capture_server):
    """Rule 2: only a case with an order replaces an earlier one. A cart without an order that comes again on a later day in other
    bytes opens a case of that day; when the page of the first day comes then, the case opened last answers, and the case of the
    first day is left as it is: not marked, in review."""
    r = capture_server
    clock = [NINE]
    r.server.deps = r.deps.model_copy(update={"clock": lambda: clock[0]})
    first = post(r, cart_page(note="<!-- day 1 -->"))[1]
    clock[0] = NINE + timedelta(days=1)
    second = post(r, cart_page(note="<!-- day 2 -->"))[1]
    assert (first["case_id"], second["case_id"]) == ("unknown_20261112T090000", "unknown_20261113T090000")
    again = post(r, cart_page(note="<!-- day 1 -->"))[1]                        # the page of the first day, on the second
    assert again == second and "replaced_by" not in capture_meta(r, first["case_id"]) and close_records(r, first["case_id"]) == []
    assert open_cases(r) == [first["case_id"], second["case_id"]] and len(case_folders(r)) == 2


def test_a_case_that_is_no_capture_case_stands(capture_server, tmp_path):
    """Rule 2: only a case of the capture server is checked again. An earlier case that holds the page and was opened otherwise (by
    the batch or an upload: no capture.json in its folder) stands, also without an order: it answers the page, and no case is
    opened for the order found now."""
    r = capture_server
    html = cart_page()
    saved = tmp_path / "cart.html"
    saved.write_bytes(html.encode("utf-8"))                                     # the bytes the server saves of the page
    upload = open_capture_case(r.settings.cases_root, saved, None, datetime(2026, 11, 12, 8, 45))
    run_case(upload, r.deps)
    assert upload.case_id == "unknown_20261112T084500" and not (upload.root / "capture.json").exists()
    exported(r, tmp_path, "order.csv", {"250981": OWN})
    assert order_of(r, tmp_path, html) == ("250981", BY_ITEMS)
    answer = post(r, html)[1]
    assert (answer["case_id"], answer["verdict"]) == (upload.case_id, "no_po") and case_folders(r) == [upload.case_id]
    assert result_of(r, upload.case_id)["status"] == "review" and close_records(r, upload.case_id) == []


def test_a_found_case_keeps_answering_when_a_second_order_is_filed(capture_server, tmp_path):
    """Q2 (g) and rule 2: a case whose order was found, by its number or as the one order of today with exactly the cart's items,
    keeps answering the same cart of that day, whatever is filed later: here a second order with the same items, with which the
    cart would now be compared as a guess. No case is opened behind the buyer, and the changes the aid prepared stay."""
    r = capture_server
    exported(r, tmp_path, "first.csv", {"250931": OWN})
    html = cart_page(prices={"KM-9001": "2,52"})
    found = post(r, html)[1]
    assert (found["po_number"], found["verdict"], len(prepared_rows(r))) == ("250931", "match", 2)
    exported(r, tmp_path, "second.csv", {"250932": OWN}, at=EIGHT + 60)
    assert order_of(r, tmp_path, html) == ("250932", NOT_SURE)
    assert post(r, html)[1] == found and post(r, cart_page(prices={"KM-9001": "2,52"}, note="<!-- other bytes -->"))[1] == found
    assert case_folders(r) == [found["case_id"]] and "replaced_by" not in capture_meta(r, found["case_id"]) and len(prepared_rows(r)) == 2
    stated = post(r, cart_page(reference="250931", prices={"KM-9001": "2,52"}))[1]         # found by its number: so it is
    exported(r, tmp_path, "third.csv", {"250933": OWN}, at=EIGHT + 120)
    assert post(r, cart_page(reference="250931", prices={"KM-9001": "2,52"}))[1] == stated and len(case_folders(r)) == 2


def test_a_guess_whose_files_cannot_be_read_is_left_as_it_is(capture_server, tmp_path):
    """Q2 (a): what was done on a guess is read from its files. While one of them cannot be read, the guess is left as it is: no
    case replaces it, also not the case that is opened for the order found now (ruling Q18: the rule is asked again for every
    guess). Once the file can be read again and shows that nothing was done, the next capture that meets the guess replaces it."""
    r = capture_server
    released(r, tmp_path, "250960", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    records = r.settings.cases_root / guess["case_id"] / "steps" / "09_close.json"
    records.write_bytes(b'{"records": [{"action": "clo')                         # torn: what was done cannot be read
    left = folder_bytes(r, guess["case_id"])
    still = post(r, html)[1]                                                    # its order is still the guess: it answers
    assert (still["case_id"], texts_of(still)[0]) == (guess["case_id"], SENT_NL.format(po="250960"))
    exported(r, tmp_path, "new.csv", {"250961": OWN})
    status, answer = post(r, html)
    assert status == 200 and (answer["po_number"], answer["case_id"]) == ("250961", "250961_20261112T090001") and len(case_folders(r)) == 2
    assert "replaced_by" not in capture_meta(r, guess["case_id"]) and folder_bytes(r, guess["case_id"]) == left
    assert sorted(open_cases(r)) == sorted([guess["case_id"], answer["case_id"]])
    records.unlink()
    found = post(r, html)[1]
    assert found == answer and capture_meta(r, guess["case_id"])["replaced_by"] == found["case_id"] and open_cases(r) == [found["case_id"]]


def test_the_server_starts_beside_a_capture_json_that_cannot_be_read(capture_server):
    """The start of the server finishes the replacements that were cut off (finish_marked). A capture.json that is torn, one that is
    no object, and a marked folder that is no case do not stop it, and are left as they are."""
    r = capture_server
    root = r.settings.cases_root
    whole = post(r, cart_page())[1]
    torn = post(r, cart_page(("KM-9003", "KM-9004")))[1]
    listed = post(r, cart_page(("KM-9005", "KM-9006")))[1]
    (root / torn["case_id"] / "capture.json").write_bytes(b'{"sha256": "torn')
    (root / listed["case_id"] / "capture.json").write_bytes(b'["no", "object"]')
    (root / "notes_20261112T080000").mkdir()
    (root / "notes_20261112T080000" / "capture.json").write_text('{"replaced_by": "250981_20261112T090000"}', encoding="utf-8")
    serve(r.settings, r.deps, "127.0.0.1", 0, False).server_close()
    assert open_cases(r) == [whole["case_id"], torn["case_id"], listed["case_id"]]
    assert (root / torn["case_id"] / "capture.json").read_bytes() == b'{"sha256": "torn'
    assert [close_records(r, answer["case_id"]) for answer in (whole, torn, listed)] == [[], [], []]


def test_the_banner_of_a_cart_without_an_order_says_what_to_do(tmp_path, madeup_out):
    """Rule 5: the banner of a cart without a PO says what to do, in the language of the settings."""
    for language, sentence in (("nl", NO_PO_NL), ("en", NO_PO_EN)):
        with running(tmp_path / language, madeup_out, settings=SETTINGS.model_copy(update={"language": language})) as r:
            status, answer = post(r, cart_page())
            assert status == 200 and (answer["verdict"], answer["message"]) == ("no_po", sentence)


class ProcessEnded(Exception):
    """The process ended at this step: nothing after it ran."""


def test_a_replacement_cut_off_is_finished_at_the_next_capture_or_start(capture_server, tmp_path, monkeypatch):
    """Amendment 5 and Task 36's ruling N20: the replaced case is marked in its capture.json before finish_replaced rewrites its
    result, so that from that moment it answers no capture and the runner reports it in no digest, whatever step the process ends
    at. A replacement cut off after the mark is finished when the same page comes again, or when the server starts; one cut off
    before the mark is made at the next capture of the page, which the new case answers."""
    r = capture_server
    real = server_module.finish_replaced

    def ended(case, by, at, language):
        raise ProcessEnded("after the mark, before the finish")
    html = cart_page()
    grey = post(r, html)[1]
    exported(r, tmp_path, "order.csv", {"250995": OWN})
    monkeypatch.setattr(server_module, "finish_replaced", ended)
    assert post(r, html)[0] == 500                                             # the process ended in the middle of the replacement
    [new] = [name for name in case_folders(r) if name.startswith("250995_")]
    assert capture_meta(r, grey["case_id"])["replaced_by"] == new and close_records(r, grey["case_id"]) == []
    assert result_of(r, grey["case_id"])["status"] == "review" and result_of(r, new)["status"] == "closed"
    assert open_cases(r) == []                                                 # marked: off the review page, its finish still missing
    state = {"last_sent": None, "reported": [grey["case_id"]], "skipped": []}
    runner._report_captures(r.settings.cases_root, state, r.deps.clock())      # marked: out of the digest's list, and never reported
    assert state["reported"] == [new] and grey["case_id"] not in state.get("captures", [])
    monkeypatch.setattr(server_module, "finish_replaced", real)
    again = post(r, html)[1]                                                    # the same page: the new case answers, the old one is finished
    assert (again["case_id"], again["verdict"]) == (new, "match") and len(case_folders(r)) == 2
    assert result_of(r, grey["case_id"])["status"] == "closed" and [rec["by"] for rec in close_records(r, grey["case_id"])] == [new]
    # cut off before the mark: the new case has run, the old one is as it was; the next capture of the page finishes it
    page_two = cart_page(("KM-9003", "KM-9004"))
    grey_two = post(r, page_two)[1]
    exported(r, tmp_path, "second.csv", {"250996": ("KM-9003", "KM-9004")})

    def ended_before(case, by, now, language):
        raise ProcessEnded("before the mark")
    monkeypatch.setattr(server_module, "_replace", ended_before)
    assert post(r, page_two)[0] == 500
    monkeypatch.undo()
    [new_two] = [name for name in case_folders(r) if name.startswith("250996_")]
    assert "replaced_by" not in capture_meta(r, grey_two["case_id"]) and result_of(r, new_two)["status"] == "closed"
    assert post(r, page_two)[1]["case_id"] == new_two and capture_meta(r, grey_two["case_id"])["replaced_by"] == new_two
    assert result_of(r, grey_two["case_id"])["status"] == "closed" and len(case_folders(r)) == 4
    # a mark without its finish is finished when the server starts
    page_three = cart_page(("KM-9001", "KM-9004"))
    grey_three = post(r, page_three)[1]
    exported(r, tmp_path, "third.csv", {"250997": ("KM-9001", "KM-9004")})
    monkeypatch.setattr(server_module, "finish_replaced", ended)
    assert post(r, page_three)[0] == 500
    monkeypatch.undo()
    [new_three] = [name for name in case_folders(r) if name.startswith("250997_")]
    assert result_of(r, grey_three["case_id"])["status"] == "review" and capture_meta(r, grey_three["case_id"])["replaced_by"] == new_three
    # a finish that fails again leaves the case marked: it answers no capture, also when the case that replaced it is gone

    def refused(case, by, at, language):
        raise OSError("made-up: the result cannot be written")
    monkeypatch.setattr(server_module, "finish_replaced", refused)
    shutil.rmtree(r.settings.cases_root / new_three)
    status, again_three = post(r, page_three)
    assert status == 200 and again_three["po_number"] == "250997" and again_three["case_id"] not in (grey_three["case_id"], new_three)
    assert capture_meta(r, grey_three["case_id"])["replaced_by"] == new_three and result_of(r, grey_three["case_id"])["status"] == "review"
    assert open_cases(r) == [] and close_records(r, grey_three["case_id"]) == []
    monkeypatch.undo()
    serve(r.settings, r.deps, "127.0.0.1", 0, False).server_close()
    assert result_of(r, grey_three["case_id"])["status"] == "closed"
    assert [record["by"] for record in close_records(r, grey_three["case_id"])] == [new_three]


def test_a_decision_in_the_instant_of_the_mark_is_taken_out_again(capture_server, tmp_path, monkeypatch):
    """Ruling Q11 (a): the review page and the capture server are two processes without a lock. A replacement that falls between
    decide's look at the mark and its append is not seen by that look, so decide looks again after it appended: the case was
    marked meanwhile, so it takes its line out again and refuses as for a marked case. Nothing is left in decisions.jsonl (the
    file is gone when it held nothing before, else it holds its earlier bytes), and the case ends as replaced."""
    r = capture_server
    looked = app_module._saved_result
    released(r, tmp_path, "260001", OWN, at=EIGHT - DAY)                        # only an order that was sent: a guess
    html = cart_page(prices={"KM-9002": "44,00"})
    grey_html = cart_page(("KM-9003", "KM-9004"))
    guess, grey = post(r, html)[1], post(r, grey_html)[1]
    assert (guess["po_number"], guess["status"], grey["po_number"], grey["status"]) == ("260001", "review", None, "review")
    case, [item] = _items(r, grey["case_id"])
    decide(case, item["item_id"], "unresolved")                                 # an earlier decision in the case without a PO
    earlier = (case.root / "decisions.jsonl").read_bytes()
    exported(r, tmp_path, "own.csv", {"260002": OWN, "260003": ("KM-9003", "KM-9004")})     # the exports of the two carts' own orders
    for answer, page_html, number in ((guess, html, "260002"), (grey, grey_html, "260003")):
        case, items = _items(r, answer["case_id"])

        def then_replaced(of, page_html=page_html):                             # after decide's look at the mark: the reload's pre-check
            monkeypatch.setattr(app_module, "_saved_result", looked)
            assert post(r, page_html)[0] == 200
            return looked(of)
        monkeypatch.setattr(app_module, "_saved_result", then_replaced)
        with pytest.raises(ValueError, match="was replaced by another case, so no decision is taken on it"):
            decide(case, items[0]["item_id"], "confirm")
        [new] = [name for name in case_folders(r) if name.startswith(number + "_")]
        assert capture_meta(r, answer["case_id"])["replaced_by"] == new and result_of(r, answer["case_id"])["status"] == "closed"
        assert [(record["action"], record["by"]) for record in close_records(r, answer["case_id"])] == [("replaced", new)]
        assert decision_text(case, items[0]["item_id"], "confirm", False, "nl") == (STRINGS["nl"]["refused"], False)
    assert not (r.settings.cases_root / guess["case_id"] / "decisions.jsonl").exists()                  # the line is gone
    assert (r.settings.cases_root / grey["case_id"] / "decisions.jsonl").read_bytes() == earlier         # and here only that line
    assert open_cases(r) == [name for name in case_folders(r) if name.startswith("260002_")]            # the new case with a difference


def test_a_close_in_the_instant_of_the_mark_leaves_a_replaced_case(capture_server, tmp_path, monkeypatch):
    """Ruling Q11 (b): close_case looks at the mark again right before it writes the result back, and refuses as for a marked case
    when the mark is there now; nothing of the close is written. When the mark comes after that second look, the close writes its
    stale result (in review) and its record after the replacement's: the next reading of the marked case (a capture that meets
    it, the server's start) finishes it again, so that it ends as a replaced case: the result closed, the replacement its last
    record."""
    r = capture_server
    released(r, tmp_path, "260001", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    exported(r, tmp_path, "own.csv", {"260002": OWN})
    case = load_case(r.settings.cases_root, guess["case_id"])
    carry_out = close_module._Close.carry_out

    def then_replaced(close):                                                   # after close_case's look at the mark, before it writes
        monkeypatch.setattr(close_module._Close, "carry_out", carry_out)
        assert post(r, html)[0] == 200
        return carry_out(close)
    monkeypatch.setattr(close_module._Close, "carry_out", then_replaced)
    with pytest.raises(ValueError, match="is not in review: another case replaced it"):
        close_case(case, r.deps)
    [new] = [name for name in case_folders(r) if name.startswith("260002_")]
    assert capture_meta(r, guess["case_id"])["replaced_by"] == new and result_of(r, guess["case_id"])["status"] == "closed"
    assert [(record["action"], record.get("by")) for record in close_records(r, guess["case_id"])] == [("replaced", new)]
    assert close_text(case, r.deps, "nl") == (STRINGS["nl"]["not_closed"], False)
    # the mark after the close's second look: the stale result and the close's record are written after the replacement's
    released(r, tmp_path, "260011", ("KM-9003", "KM-9004"), at=EIGHT - DAY)
    html_two = cart_page(("KM-9003", "KM-9004"), prices={"KM-9004": "19,00"})
    guess_two = post(r, html_two)[1]
    exported(r, tmp_path, "second.csv", {"260012": ("KM-9003", "KM-9004")})
    case_two = load_case(r.settings.cases_root, guess_two["case_id"])
    marked = close_module.marked_replaced
    looks = []

    def replaced_after_the_look(root):                                          # the second look sees no mark; the replacement follows it
        seen = marked(root)
        looks.append(seen)
        if len(looks) == 2:
            assert post(r, html_two)[0] == 200
        return seen
    monkeypatch.setattr(close_module, "marked_replaced", replaced_after_the_look)
    assert close_case(case_two, r.deps).status == "review" and looks == [False, False]
    monkeypatch.undo()
    [new_two] = [name for name in case_folders(r) if name.startswith("260012_")]
    assert capture_meta(r, guess_two["case_id"])["replaced_by"] == new_two and result_of(r, guess_two["case_id"])["status"] == "review"
    assert [record["action"] for record in close_records(r, guess_two["case_id"])] == ["replaced", "close"]
    assert open_cases(r) == [name for name in (new, new_two) if result_of(r, name)["status"] == "review"]
    again = post(r, html_two.replace("<h1>", "<!-- other bytes --><h1>"))[1]    # a capture meets the marked case: it is finished again
    assert again["case_id"] == new_two and result_of(r, guess_two["case_id"])["status"] == "closed"
    records = close_records(r, guess_two["case_id"])
    assert [record["action"] for record in records] == ["replaced", "close", "replaced"] and records[-1]["by"] == new_two
    assert capture_meta(r, guess_two["case_id"])["replaced_by"] == new_two
    # and the server's start does the same
    case_two.write_step(8, "result", json.loads((case_two.root / "steps" / "08_result.json").read_text(encoding="utf-8")) | {"status": "review"})
    close_module._add_record(case_two, {"action": "close", "at": NINE.isoformat(), "decisions": {}, "carried_out": {}, "write_entries": [],
                                        "messages": [], "status": "review", "error": None, "stop": None, "reapproval_pending": None,
                                        "settled": [], "question_open": False})
    serve(r.settings, r.deps, "127.0.0.1", 0, False).server_close()
    assert result_of(r, guess_two["case_id"])["status"] == "closed" and close_records(r, guess_two["case_id"])[-1]["action"] == "replaced"
    # a close in that instant that closed the case (a case without a PO, its one item decided): the result is closed, but by the
    # buyer's close, written after the replacement's record; the next reading finishes the case again, without a supplier
    grey_html = cart_page(("KM-9005", "KM-9006"))
    grey = post(r, grey_html)[1]
    case_three, [item] = _items(r, grey["case_id"])
    decide(case_three, item["item_id"], "keep")
    exported(r, tmp_path, "third.csv", {"260022": ("KM-9005", "KM-9006")})
    looks.clear()

    def replaced_after_the_look_too(root):
        seen = marked(root)
        looks.append(seen)
        if len(looks) == 2:
            assert post(r, grey_html)[0] == 200
        return seen
    monkeypatch.setattr(close_module, "marked_replaced", replaced_after_the_look_too)
    assert close_case(case_three, r.deps).status == "closed"
    monkeypatch.undo()
    [new_three] = [name for name in case_folders(r) if name.startswith("260022_")]
    held = result_of(r, grey["case_id"])
    assert (held["status"], held["supplier"]) == ("closed", "S22") and capture_meta(r, grey["case_id"])["replaced_by"] == new_three
    assert [record["action"] for record in close_records(r, grey["case_id"])] == ["replaced", "close"]
    assert post(r, grey_html.replace("<h1>", "<!-- other bytes --><h1>"))[1]["case_id"] == new_three
    held = result_of(r, grey["case_id"])
    assert (held["status"], held["supplier"]) == ("closed", None)
    assert [record["action"] for record in close_records(r, grey["case_id"])] == ["replaced", "close", "replaced"]
    # a guess the buyer acted on is never finished as replaced, whatever its files say: one that is marked, whose result says
    # closed and that holds no record of a close (a state made by hand) has its mark taken back and is left as it is
    released(r, tmp_path, "260031", ("KM-9007",), at=EIGHT - DAY)
    acted_html = cart_page(("KM-9007",), prices={"KM-9007": "99,50"})
    acted = post(r, acted_html)[1]
    case_four, items_four = _items(r, acted["case_id"])
    decide(case_four, items_four[0]["item_id"], "unresolved")
    path = case_four.root / "capture.json"
    held_meta = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(held_meta | {"replaced_by": "260032_20261112T093000"}), encoding="utf-8")
    case_four.write_step(8, "result", result_of(r, acted["case_id"]) | {"status": "closed"})
    left = {name: data for name, data in folder_bytes(r, acted["case_id"]).items() if name != "capture.json"}
    serve(r.settings, r.deps, "127.0.0.1", 0, False).server_close()
    assert capture_meta(r, acted["case_id"]) == held_meta and close_records(r, acted["case_id"]) == []
    assert {name: data for name, data in folder_bytes(r, acted["case_id"]).items() if name != "capture.json"} == left


def test_the_close_looks_at_the_mark_after_it_read_the_result_again(capture_server, tmp_path, monkeypatch):
    """Ruling Q11 (b) together with Task 44's reading again of the saved result (its S6): in one close the saved result is read
    again first, and the mark is looked at after that reading and right before the write. A replacement that falls while the
    result is read again is therefore seen: the close is refused as for a marked case and writes nothing, so the case stays
    finished as replaced. A close during which nothing replaced the case reads again and writes as before."""
    r = capture_server
    released(r, tmp_path, "260001", OWN, at=EIGHT - DAY)
    html = cart_page(prices={"KM-9002": "44,00"})
    guess = post(r, html)[1]
    exported(r, tmp_path, "own.csv", {"260002": OWN})
    case = load_case(r.settings.cases_root, guess["case_id"])
    refresh, order = close_module._Close.refresh, []

    def then_replaced(close):                                                   # the reload's pre-check falls right after the reading again
        refresh(close)
        if inspect.stack()[1].function == "save":                               # the reading again right before the write
            order.append("read again")
            assert post(r, html)[0] == 200
    looked = close_module.marked_replaced

    def look(root):
        order.append("mark")
        return looked(root)
    monkeypatch.setattr(close_module._Close, "refresh", then_replaced)
    monkeypatch.setattr(close_module, "marked_replaced", look)
    with pytest.raises(ValueError, match="is not in review: another case replaced it"):
        close_case(case, r.deps)
    monkeypatch.undo()
    assert order == ["mark", "read again", "mark"]                              # the look of close_case, the reading again, the second look
    [new] = [name for name in case_folders(r) if name.startswith("260002_")]
    assert capture_meta(r, guess["case_id"])["replaced_by"] == new and result_of(r, guess["case_id"])["status"] == "closed"
    assert [(record["action"], record.get("by")) for record in close_records(r, guess["case_id"])] == [("replaced", new)]
    # a close that nothing disturbs: the result is read again, the mark looked at, and the close written
    other = post(r, cart_page(("KM-9003", "KM-9004")))[1]
    case_two, [item] = _items(r, other["case_id"])
    decide(case_two, item["item_id"], "keep")
    order.clear()

    def counted(close):
        refresh(close)
        if inspect.stack()[1].function == "save":
            order.append("read again")
    monkeypatch.setattr(close_module._Close, "refresh", counted)
    monkeypatch.setattr(close_module, "marked_replaced", look)
    assert close_case(case_two, r.deps).status == "closed" and order == ["mark", "read again", "mark"]
    assert [record["action"] for record in close_records(r, other["case_id"])] == ["close"]


CUTS = ("before the result is written", "between the result and the record", "after the record is written")


def _cut_off_inside_the_finish(folder: Path, madeup_out, monkeypatch, cut: str, earlier: str):
    """A capture case, `earlier` a case without a PO ("grey") or a guess, whose replacement was cut off inside finish_replaced of
    the review page's close at `cut`: before the closed result is written (that write fails), between the result and the record
    (_add_record fails, as on a file that another program holds), or right after the record (the process ends there). The deps, the
    settings, the clock, the page, the earlier case's id and the id of the case that replaced it."""
    d = deps(folder, madeup_out, settings=SETTINGS.model_copy(update={"digest": "daily", "digest_time": "16:30"}))
    now = [NINE]
    s = d.settings.model_copy(update={"po_store_root": d.store.root, "watched_folder": folder / "inbox"})
    d = d.model_copy(update={"settings": s, "clock": lambda: now[0]})
    r = Running("", None, d, s)
    if earlier == "guess":
        exported(r, folder, "earlier.csv", {"250997": OWN}, at=EIGHT - DAY)
    html = cart_page()
    old = precheck(html, SHOP, d, s)
    assert (old.po_number, old.status) == (("250997", "review") if earlier == "guess" else (None, "review")), (cut, earlier)
    exported(r, folder, "order.csv", {"250998": OWN})
    write_step, add_record = Case.write_step, close_module._add_record

    def no_result(case, number, name, data):
        if case.case_id == old.case_id and name == "result":
            raise PermissionError("made-up: the result is held by another program")
        return write_step(case, number, name, data)

    def no_record(case, record):
        raise PermissionError("made-up: the records are held by another program")

    def ended_after(case, record):
        add_record(case, record)
        raise ProcessEnded("after the record")
    if cut == CUTS[0]:
        monkeypatch.setattr(Case, "write_step", no_result)
    else:
        monkeypatch.setattr(close_module, "_add_record", no_record if cut == CUTS[1] else ended_after)
    with pytest.raises((PermissionError, ProcessEnded)):
        precheck(html, SHOP, d, s)
    monkeypatch.undo()
    [new] = [name for name in case_folders(r) if name.startswith("250998_")]
    assert capture_meta(r, old.case_id)["replaced_by"] == new, (cut, earlier)
    assert result_of(r, old.case_id)["status"] == ("review" if cut == CUTS[0] else "closed"), (cut, earlier)
    assert [record["action"] for record in close_records(r, old.case_id)] == (["replaced"] if cut == CUTS[2] else []), (cut, earlier)
    return d, s, now, html, old.case_id, new


def _ends_as_replaced(r: Running, old: str, new: str, folder: Path, now: list, said) -> None:
    """The earlier case is finished as replaced by the new one, and the digest of that day names the new case only."""
    assert capture_meta(r, old)["replaced_by"] == new and open_cases(r) == [], said
    assert (result_of(r, old)["status"], result_of(r, old)["supplier"]) == ("closed", None), said
    assert [(record["action"], record["by"]) for record in close_records(r, old)] == [("replaced", new)], said
    now[0] = datetime(2026, 11, 12, 16, 31)
    runner.run_loop(r.deps, folder / "STOP", poll_seconds=0, max_passes=1)
    text = (r.settings.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "voorcontrole PO 250998 (S22): afgesloten, geen schrijfacties" in text, said
    assert "PO onbekend" not in text and "250997" not in text and old not in text, said
    state = json.loads((r.settings.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert old not in state["reported"] + state.get("captures", []), said


@pytest.mark.parametrize("cut", CUTS)
def test_a_replacement_cut_off_inside_its_finish_is_finished_by_the_next_capture(tmp_path, madeup_out, monkeypatch, cut):
    """Ruling Q15 (the check's finding of a regression): a replacement that was cut off at any point after the mark is finished by
    the next capture that meets the earlier case, and never undone: the mark stays, the result is closed and the replacement is
    recorded once, for a case without a PO and for a guess. A case without a PO that is marked, whose result is closed and that
    holds no close record of the buyer, is such a cut-off, not a case the buyer closed. The digest names the new case only."""
    for earlier in ("grey", "guess"):
        folder = tmp_path / earlier
        d, s, now, html, old, new = _cut_off_inside_the_finish(folder, madeup_out, monkeypatch, cut, earlier)
        r = Running("", None, d, s)
        again = precheck(html.replace("<h1>", "<!-- the same cart in other bytes --><h1>"), SHOP, d, s)
        assert (again.case_id, again.verdict) == (new, "match"), (cut, earlier)
        _ends_as_replaced(r, old, new, folder, now, (cut, earlier))


@pytest.mark.parametrize("cut", CUTS)
def test_a_replacement_cut_off_inside_its_finish_is_finished_by_the_servers_start(tmp_path, madeup_out, monkeypatch, cut):
    """Ruling Q15, the second reading: the server's start (finish_marked) finishes such a replacement as the next capture does."""
    for earlier in ("grey", "guess"):
        folder = tmp_path / earlier
        d, s, now, html, old, new = _cut_off_inside_the_finish(folder, madeup_out, monkeypatch, cut, earlier)
        r = Running("", None, d, s)
        serve(s, d, "127.0.0.1", 0, False).server_close()
        _ends_as_replaced(r, old, new, folder, now, (cut, earlier))


def test_a_cut_off_finish_is_finished_also_after_a_close_press_that_closed_nothing(capture_server, tmp_path, monkeypatch):
    """Ruling Q15, found by the search: the buyer pressed "Zaak afsluiten" on a case without a PO with nothing decided, which left
    it in review and is no act (Q2 (a)); its record of that press holds the status review. When the case's replacement is then cut
    off between the closed result and the record, the closed result is the finish's, not the buyer's: the next reading finishes
    the replacement. A case without a PO that the buyer did close holds a close record with the status closed, and keeps standing
    as he left it, its mark taken back."""
    r = capture_server
    html = cart_page()
    grey = post(r, html)[1]
    case = load_case(r.settings.cases_root, grey["case_id"])
    assert close_case(case, r.deps).status == "review"                         # a press with nothing decided
    [press] = close_records(r, grey["case_id"])
    assert (press["action"], press["status"], press["carried_out"]) == ("close", "review", {})
    exported(r, tmp_path, "order.csv", {"250998": OWN})

    def no_record(of, record):
        raise PermissionError("made-up: the records are held by another program")
    monkeypatch.setattr(close_module, "_add_record", no_record)
    assert post(r, html)[0] == 500
    monkeypatch.undo()
    [new] = [name for name in case_folders(r) if name.startswith("250998_")]
    assert capture_meta(r, grey["case_id"])["replaced_by"] == new and result_of(r, grey["case_id"])["status"] == "closed"
    assert [record["action"] for record in close_records(r, grey["case_id"])] == ["close"]
    again = post(r, html)[1]
    assert again["case_id"] == new and capture_meta(r, grey["case_id"])["replaced_by"] == new
    assert [(record["action"], record.get("by")) for record in close_records(r, grey["case_id"])] == [("close", None), ("replaced", new)]
    assert result_of(r, grey["case_id"])["supplier"] is None and open_cases(r) == []
    # a case without a PO that the buyer closed, and a mark on it without anything else: the mark is taken back
    pair = cart_page(("KM-9003", "KM-9004"))
    closed = post(r, pair)[1]
    _decide_all(r, closed["case_id"], "keep")
    assert result_of(r, closed["case_id"])["status"] == "closed" and close_records(r, closed["case_id"])[-1]["status"] == "closed"
    path = r.settings.cases_root / closed["case_id"] / "capture.json"
    held = path.read_bytes()
    path.write_text(json.dumps(json.loads(held) | {"replaced_by": "250999_20261112T093000"}), encoding="utf-8")
    left = {name: data for name, data in folder_bytes(r, closed["case_id"]).items() if name != "capture.json"}
    assert post(r, pair)[1]["case_id"] == closed["case_id"]                    # it answers: it was not replaced
    assert "replaced_by" not in capture_meta(r, closed["case_id"])
    assert {name: data for name, data in folder_bytes(r, closed["case_id"]).items() if name != "capture.json"} == left


def test_no_digest_names_a_replaced_capture_case(tmp_path, madeup_out, monkeypatch):
    """Amendment 5 (Task 36's ruling N20 for a capture case): a grey capture case that the runner listed for its next digest leaves
    that list once it is replaced, and the digest names the case that replaced it, never the replaced one. So it is when the
    replacement was cut off right after the mark, when the case is replaced after the runner looked at the capture cases and before
    it made the digest of the same pass (the new case is then in the next digest), and when the capture.json of the replaced case
    cannot be read at the pass: the runner then knows the replacement by the case's close record (ruling Q6)."""
    def ended(case, by, at, language):
        raise ProcessEnded("after the mark, before the finish")
    for where in ("replaced", "cut off after the mark", "replaced during the pass", "replaced, its capture.json torn",
                  "replaced during the pass, its capture.json torn"):
        folder = tmp_path / where.replace(" ", "_")
        d = deps(folder, madeup_out, settings=SETTINGS.model_copy(update={"digest": "daily", "digest_time": "16:30"}))
        now = [datetime(2026, 11, 12, 9, 0)]
        s = d.settings.model_copy(update={"po_store_root": d.store.root, "watched_folder": folder / "inbox"})
        d = d.model_copy(update={"settings": s, "clock": lambda: now[0]})
        r = Running("", None, d, s)
        html = cart_page()
        grey = precheck(html, SHOP, d, s)
        runner.run_loop(d, folder / "STOP", poll_seconds=0, max_passes=1)       # the runner lists the grey case for its next digest
        state = json.loads((s.cases_root / "digest_state.json").read_text(encoding="utf-8"))
        assert (grey.verdict, state["reported"], state["captures"]) == ("no_po", [grey.case_id], [grey.case_id]), where
        exported(r, folder, "order.csv", {"250998": OWN})
        now[0] = datetime(2026, 11, 12, 16, 31)                                 # the digest is due at the next pass
        if where == "replaced":
            assert precheck(html, SHOP, d, s).verdict == "match"
        elif where == "replaced, its capture.json torn":
            assert precheck(html, SHOP, d, s).verdict == "match"
            (s.cases_root / grey.case_id / "capture.json").write_bytes(b'{"sha256": "torn')
        elif where == "cut off after the mark":
            monkeypatch.setattr(server_module, "finish_replaced", ended)
            with pytest.raises(ProcessEnded):
                precheck(html, SHOP, d, s)
        else:
            looked = runner._report_captures

            def then_replaced(root, state, at, torn=where.endswith("torn")):
                looked(root, state, at)
                assert precheck(html, SHOP, d, s).verdict == "match"
                if torn:
                    (s.cases_root / grey.case_id / "capture.json").write_bytes(b'{"sha256": "torn')
            monkeypatch.setattr(runner, "_report_captures", then_replaced)
        runner.run_loop(d, folder / "STOP", poll_seconds=0, max_passes=1)
        monkeypatch.undo()
        text = (s.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
        assert "PO onbekend" not in text and grey.case_id not in text, where
        assert ("voorcontrole PO 250998 (S22): afgesloten, geen schrijfacties" in text) == (not where.startswith("replaced during")), where
        state = json.loads((s.cases_root / "digest_state.json").read_text(encoding="utf-8"))
        assert state["reported"] == [] and state["last_sent"] is not None, where
        if where.startswith("replaced during the pass"):                        # the case that replaced it is in the next digest
            now[0] = datetime(2026, 11, 13, 16, 31)
            runner.run_loop(d, folder / "STOP", poll_seconds=0, max_passes=1)
            later = (s.cases_root / "digests" / "2026-11-13.txt").read_text(encoding="utf-8")
            assert "voorcontrole PO 250998 (S22): afgesloten, geen schrijfacties" in later and "PO onbekend" not in later


def test_a_case_replaced_during_the_pass_leaves_no_empty_digest_per_case(tmp_path, madeup_out, monkeypatch):
    """With a digest per case: a grey capture case that is replaced after the runner looked at the capture cases, in the pass that
    would have reported it, leaves the cases to report, so that the pass sends no digest that names nothing. The case that replaced
    it is in the digest of the next pass, alone."""
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"digest": "per_case"}))
    now = [NINE]
    s = d.settings.model_copy(update={"po_store_root": d.store.root, "watched_folder": tmp_path / "inbox"})
    d = d.model_copy(update={"settings": s, "clock": lambda: now[0]})
    html = cart_page()
    grey = precheck(html, SHOP, d, s)
    exported(Running("", None, d, s), tmp_path, "order.csv", {"250998": OWN})
    looked = runner._report_captures

    def then_replaced(root, state, at):
        looked(root, state, at)
        assert precheck(html, SHOP, d, s).verdict == "match"
    monkeypatch.setattr(runner, "_report_captures", then_replaced)
    now[0] = NINE + timedelta(minutes=1)
    runner.run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    monkeypatch.undo()
    digests = s.cases_root / "digests"
    assert not digests.exists() or list(digests.iterdir()) == []                # the one case to report was replaced: no digest
    now[0] = NINE + timedelta(minutes=2)
    runner.run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    text = (digests / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "voorcontrole PO 250998 (S22): afgesloten, geen schrijfacties" in text and "PO onbekend" not in text and grey.case_id not in text
    assert json.loads((s.cases_root / "digest_state.json").read_text(encoding="utf-8"))["reported"] == []


def test_the_runner_never_reports_a_replaced_case_whose_capture_json_cannot_be_read(tmp_path, madeup_out):
    """Ruling Q6: the runner knows a replaced capture case by its close record as well. A case that was replaced and finished before
    the runner ever saw it, and whose capture.json cannot be read at the pass, is not reported: it is in no digest, and the state
    never lists it among the captures reported."""
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"digest": "per_case"}))
    s = d.settings.model_copy(update={"po_store_root": d.store.root, "watched_folder": tmp_path / "inbox"})
    d = d.model_copy(update={"settings": s})
    r = Running("", None, d, s)
    html = cart_page()
    grey = precheck(html, SHOP, d, s)
    exported(r, tmp_path, "order.csv", {"250998": OWN})
    found = precheck(html, SHOP, d, s)
    assert found.verdict == "match" and [rec["action"] for rec in close_records(r, grey.case_id)] == ["replaced"]
    (s.cases_root / grey.case_id / "capture.json").write_bytes(b'{"sha256": "torn')
    runner.run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=1)
    state = json.loads((s.cases_root / "digest_state.json").read_text(encoding="utf-8"))
    assert (state["captures"], state["reported"]) == ([found.case_id], [])
    text = (s.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
    assert "voorcontrole PO 250998 (S22): afgesloten, geen schrijfacties" in text and "PO onbekend" not in text


def test_the_runner_recovers_a_case_finished_as_replaced_without_reporting_it(tmp_path, madeup_out):
    """The runner's recovery (_recover) knows a replaced case by its close record as well as by its mark (the check's mutant S1):
    a capture case finished as replaced that lacks dispositions.csv and whose capture.json cannot be read has its dispositions
    recorded and is reported in no digest, and so is a confirmation case finished as replaced, which has no capture.json at all."""
    d = deps(tmp_path, madeup_out, settings=SETTINGS.model_copy(update={"digest": "daily", "digest_time": "16:30"}))
    now = [NINE]
    s = d.settings.model_copy(update={"po_store_root": d.store.root, "watched_folder": tmp_path / "inbox"})
    d = d.model_copy(update={"settings": s, "clock": lambda: now[0]})
    r = Running("", None, d, s)
    html = cart_page()
    grey = precheck(html, SHOP, d, s)
    exported(r, tmp_path, "order.csv", {"250998": OWN})
    found = precheck(html, SHOP, d, s)
    assert found.po_number == "250998" and capture_meta(r, grey.case_id)["replaced_by"] == found.case_id
    (s.cases_root / grey.case_id / "dispositions.csv").unlink()
    (s.cases_root / grey.case_id / "capture.json").write_bytes(b'{"sha256": "torn')
    reply = confirmed(r, "250998")                                              # a confirmation case, finished as replaced
    close_module.finish_replaced(reply, "250998_20261112T091500", NINE, "nl")
    (reply.root / "dispositions.csv").unlink(missing_ok=True)
    assert not (reply.root / "capture.json").exists() and [rec["action"] for rec in close_records(r, reply.case_id)] == ["replaced"]
    state = {"last_sent": None, "reported": [], "skipped": []}
    for case_id in (grey.case_id, reply.case_id):
        runner._recover(s.cases_root, state, case_id)
        assert (s.cases_root / case_id / "dispositions.csv").is_file() and state["reported"] == [], case_id
    runner._recover(s.cases_root, state, found.case_id)                         # a case that was not replaced is reported
    assert state["reported"] == [found.case_id]


def test_the_runner_reports_no_marked_capture_case_that_it_recovers(tmp_path, madeup_out, monkeypatch):
    """The runner's third place for a replaced capture case (_recover): a case with a result and no dispositions.csv, as the server
    leaves one when it ends right after the run, has its dispositions recorded by the runner, and is reported in no digest when it
    is marked as replaced: the case that replaced it is the one reported, with a daily digest and with a digest per case."""
    def ended(case, by, at, language):
        raise ProcessEnded("after the mark, before the finish")
    for digest in ("daily", "per_case"):
        folder = tmp_path / digest
        d = deps(folder, madeup_out, settings=SETTINGS.model_copy(update={"digest": digest, "digest_time": "16:30"}))
        now = [NINE]
        s = d.settings.model_copy(update={"po_store_root": d.store.root, "watched_folder": folder / "inbox"})
        d = d.model_copy(update={"settings": s, "clock": lambda: now[0]})
        r = Running("", None, d, s)
        html = cart_page()
        grey = precheck(html, SHOP, d, s)
        (s.cases_root / grey.case_id / "dispositions.csv").unlink()
        exported(r, folder, "order.csv", {"250998": OWN})
        monkeypatch.setattr(server_module, "finish_replaced", ended)
        with pytest.raises(ProcessEnded):
            precheck(html, SHOP, d, s)
        monkeypatch.undo()
        [new] = [name for name in case_folders(r) if name.startswith("250998_")]
        assert capture_meta(r, grey.case_id)["replaced_by"] == new and result_of(r, grey.case_id)["status"] == "review", digest
        now[0] = NINE + timedelta(minutes=1)
        runner.run_loop(d, folder / "STOP", poll_seconds=0, max_passes=1)
        state = json.loads((s.cases_root / "digest_state.json").read_text(encoding="utf-8"))
        assert (s.cases_root / grey.case_id / "dispositions.csv").is_file(), digest
        assert (state["captures"], state["reported"]) == ([new], [new] if digest == "daily" else []), digest
        now[0] = datetime(2026, 11, 12, 16, 31)
        runner.run_loop(d, folder / "STOP", poll_seconds=0, max_passes=1)
        text = (s.cases_root / "digests" / "2026-11-12.txt").read_text(encoding="utf-8")
        assert "voorcontrole PO 250998 (S22): afgesloten, geen schrijfacties" in text and "PO onbekend" not in text, digest
        log = s.cases_root / "runner.log"
        assert not log.exists() or log.read_text(encoding="utf-8") == "", digest


# --- the try-out cart: served and taken in the test mode only (Task 41, rule 6 and amendment 3) ---

OPEN = SETTINGS.model_copy(update={"test_mode": True, "authorization_limit": None})       # the settings under which try-outs are offered
TODAY = date(2026, 11, 12)


def test_a_try_out_cart_is_served_in_the_test_mode_only_and_by_a_safe_name(tmp_path, madeup_out):
    """Rule 6 and amendment 3: the capture server serves GET /tryout/<name>.html from <cases_root>/tryout, for a name without path
    parts, while try-outs are offered: test_mode true and no authorization limit set, as the part "Proefdraaien" offers one, with or
    without --testshop. Any other name is 404, also a folder of such a name and a name the file system refuses, and so is every
    name while try-outs are not offered. The route answers this PC's own host only: a Host of 127.0.0.1 or localhost, alone or with
    any port, as the review page's own-host check asks; any other Host, none, or more than one is 404. The settings file is read
    again before the page is served, so that the switch of the test mode on the settings tab counts at once."""
    with running(tmp_path / "open", madeup_out, testshop=False, settings=OPEN) as r:
        folder = r.settings.cases_root / "tryout"
        po = r.deps.store.as_it_stands("250201")
        name = write_cart(r.settings.cases_root, po, "price_small", TODAY)
        assert name == "250201_price_small.html" and sorted(path.name for path in folder.iterdir()) == [name]
        status, body = get(r, f"/tryout/{name}")
        assert status == 200 and body == (folder / name).read_bytes() == cart_html(po, "price_small", TODAY).encode("utf-8")
        (folder / "IB 26#1_none.html").write_bytes(body)                         # a number that an address writes in another form
        assert get(r, "/tryout/IB%2026%231_none.html") == (200, body)
        (folder / "notes.txt").write_text("made-up", encoding="utf-8")
        (folder / "sub").mkdir()
        shutil.copy(folder / name, folder / "sub" / name)
        shutil.copy(folder / name, r.settings.cases_root / "outside.html")
        (folder / "folder.html").mkdir()                                         # a folder named as a page is no page
        for path in (f"/tryout/../tryout/{name}", "/tryout/../outside.html", "/tryout/%2e%2e%2foutside.html", "/tryout/..%5coutside.html",
                     f"/tryout/sub/{name}", f"/tryout/sub%2f{name}", "/tryout/notes.txt", "/tryout/MISSING.html", "/tryout/", "/tryout/sub",
                     f"/tryout/{name}/", "/tryout/.html", "/tryout", "/tryout/folder.html", "/tryout/" + "a" * 295 + ".html",
                     "/tryout/" + "a" * 250 + ".html", "/tryout/%00.html", f"/tryout/{name}%00"):
            assert raw_request(r, "GET", path, {})[0] == 404, path[:40]
        assert not (r.settings.cases_root / "capture.log").exists()             # a name the file system refuses is no error of the server
        port = r.server.server_address[1]
        for host in (f"127.0.0.1:{port}", "127.0.0.1", "localhost", f"localhost:{port}", "LocalHost:1", "127.0.0.1:65535"):
            assert raw_request(r, "GET", f"/tryout/{name}", {"Host": host}, host=False)[0] == 200, host
        for host in (f"rebind.example:{port}", "rebind.example", "127.0.0.1.rebind.example", "localhost.rebind.example:80",
                     "rebind.example:127.0.0.1", "127.0.0.1:", "127.0.0.1:80a", "[::1]", "0.0.0.0", "127.0.0.2", "", ["127.0.0.1", "127.0.0.1"],
                     ["127.0.0.1", "rebind.example"]):
            assert raw_request(r, "GET", f"/tryout/{name}", {"Host": host}, host=False)[0] == 404, host
        assert raw_request(r, "GET", f"/tryout/{name}", {}, host=False)[0] == 404                # no Host at all
        assert raw_request(r, "GET", "/health", {"Host": "rebind.example"}, host=False)[0] == 200       # the other routes as before
        assert get(r, "/testshop/CART02.html")[0] == 404 and get(r, "/health")[0] == 200
    for n, closed in enumerate(({"test_mode": False}, {"authorization_limit": Decimal("5000")},
                                {"test_mode": False, "authorization_limit": Decimal("5000")})):
        with running(tmp_path / f"closed_{n}", madeup_out, settings=OPEN.model_copy(update=closed)) as r:
            name = write_cart(r.settings.cases_root, r.deps.store.as_it_stands("250201"), "none", TODAY)
            assert get(r, f"/tryout/{name}")[0] == 404 and get(r, "/testshop/CART02.html")[0] == 200, closed
    with watching(tmp_path / "file", madeup_out) as (r, path, rebuilt):          # SETTINGS: no test mode, and a limit
        name = write_cart(r.settings.cases_root, r.deps.store.as_it_stands("250201"), "none", TODAY)
        assert get(r, f"/tryout/{name}")[0] == 404
        data = json.loads(path.read_text(encoding="utf-8")) | {"test_mode": True, "authorization_limit": None}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        touch_later(path, 1)
        assert get(r, f"/tryout/{name}")[0] == 200 and r.server.settings.test_mode is True


def test_a_try_out_cart_is_taken_in_the_test_mode_only(tmp_path, madeup_out):
    """Amendment 3: the capture server refuses a try-out cart page while try-outs are not offered, as the scan skips a try-out
    document: 403, no case and no copy of the page, also for a page that a case made in the test mode would answer. The answer's
    reason, which the bar shows, is a sentence in the settings' language; the line in capture.log is the scan's reason, as it was.
    A real cart is taken as before. In the test mode the try-out's case answers its page, and a real cart with the same lines is
    never answered from it, nor the try-out from a real cart's case."""
    with running(tmp_path, madeup_out, settings=OPEN) as r:
        po = r.deps.store.as_it_stands("250201")
        tryout = cart_html(po, "none", TODAY)
        real = tryout.replace("Proefwinkel (verzonnen pagina)", "S21")
        status, answer = post(r, tryout)
        assert status == 200 and (answer["verdict"], answer["po_number"]) == ("match", "250201")
        assert tryout_case(load_case(r.settings.cases_root, answer["case_id"]))
        own = post(r, real)[1]                                                  # the same lines in a real shop's page: a case of its own
        assert own["case_id"] != answer["case_id"] and not tryout_case(load_case(r.settings.cases_root, own["case_id"]))
        assert post(r, tryout.replace("<body>", "<body><!-- again -->"))[1]["case_id"] == answer["case_id"]
        assert post(r, real.replace("<body>", "<body><!-- again -->"))[1]["case_id"] == own["case_id"]
        cases, kept = case_folders(r), captures(r)
        for closed, said in (({"test_mode": False}, "Dit is een proefwinkelwagen: de assistent controleert hem alleen in de proefstand."),
                             ({"authorization_limit": Decimal("5000"), "language": "en"},
                              "This is a try-out cart: the assistant checks it only in the test mode.")):
            r.server.settings = r.settings.model_copy(update=closed)
            r.server.deps = r.deps.model_copy(update={"settings": r.server.settings})
            for html in (tryout, tryout.replace("<body>", "<body><!-- other bytes -->"), cart_html(po, "price_small", TODAY)):
                assert post(r, html) == (403, {"error": said}), closed
            assert case_folders(r) == cases and captures(r) == kept, closed
            assert post(r, real)[1]["case_id"] == own["case_id"]                # a real cart is answered as before
        log = (r.settings.cases_root / "capture.log").read_text(encoding="utf-8").splitlines()
        assert len(log) == 6 and all(line.endswith("403 POST /capture: try-out document outside the test mode") for line in log)
        assert TRYOUT_OUTSIDE == "try-out document outside the test mode"
        # the refusal comes before anything else is done with the page: an earlier case of it that has no result is not run for it
        folder = r.settings.cases_root / answer["case_id"]
        (folder / "steps" / "08_result.json").unlink()
        (folder / "dispositions.csv").unlink()
        assert post(r, tryout)[0] == 403 and not (folder / "steps" / "08_result.json").exists()
        r.server.settings, r.server.deps = r.settings, r.deps                   # in the test mode again: the case is run and answers
        resumed = post(r, tryout)[1]
        assert (resumed["case_id"], resumed["verdict"]) == (answer["case_id"], "match") and (folder / "steps" / "08_result.json").is_file()


def test_the_documents_say_what_the_pre_check_does_with_the_order_of_a_cart():
    """The documents of Task 41, each by a sentence that this task wrote: no string below stood in the documents before it. The
    manual quotes the banner's lines as the server gives them and says when the aid is sure of an order (section 2.5), names the
    reason of a guessed cart with its labels and what helps when the order is the wrong one (2.4), the rule of the cart's order, the
    earlier case that answers, the mark and its finish, and the try-out shop (3.13), the try-out cart with its button, its question,
    its note and its walk (3.17), what remains a limitation (4.9), the entry of the troubleshooting (5) and the buyer's guide (7);
    the README and the extension's README say the same in short. The second review round added what a cart check does not compare,
    the title of a guess, the lines digest and the cases of the same lines, the guess that is never replaced once the buyer acted
    on it, the cut-off that is finished and never undone, and one word for the assistant in the guide."""
    manual = " ".join(Path("aid/MANUAL.md").read_text(encoding="utf-8").split())
    nl = STRINGS["nl"]
    outside = "Dit is een proefwinkelwagen: de assistent controleert hem alleen in de proefstand."
    assert server_module._TRYOUT_OUTSIDE["nl"] == outside and server_module._GUESSES["nl"] == {NOT_SURE: NOT_SURE_NL, ALREADY_SENT: SENT_NL}
    assert server_module._GUESSES["en"] == {NOT_SURE: NOT_SURE_EN, ALREADY_SENT: SENT_EN}
    for said in (NO_PO_NL, NOT_SURE_NL.format(po="250922"), SENT_NL.format(po="250950"), outside, nl["tryout_intro"], NOT_COMPARED["nl"],
                 f"Winkelwagen komt overeen met inkooporder 250931{CHOSEN_NL}", nl["tryout_cart_make"], nl["tryout_cart_change"],
                 nl["tryout_cart_made"].format(link="http://127.0.0.1:7870/tryout/250108_price_small.html"),
                 nl["tryout_refused"].format(reason=nl["tryout_reason:not_found"]),
                 "Inkooporder 250201, regel 1 (KM-2101): prijs 0,95 → 0,96 (proefdocument)",
                 "403 POST /capture: try-out document outside the test mode"):
        assert f'"{said}"' in manual, said
    assert f"| {GUESSED_CART} | {CART_GUESS_NL} | {CART_GUESS_EN} |" in manual
    assert server_module._NOT_COMPARED == NOT_COMPARED and server_module.GUESS_TITLE == GUESS_TITLE
    assert "because the cart at the supplier differs from the order" in manual[:manual.index("### 2.4 What the review reasons mean")]
    sections = {name: manual[manual.index(first):manual.index(then)] for name, first, then in (
        ("2.4", "### 2.4 What the review reasons mean", "### 2.5 The pre-check in the browser"),
        ("2.5", "### 2.5 The pre-check in the browser", "### 2.6 The tab In Exact invoeren"),
        ("3.13", "### 3.13 The capture server", "### 3.14 Connecting the work mailbox"),
        ("3.17", "### 3.17 Connecting to Exact", "## 4. Maintaining it"),
        ("4.9", "### 4.9 Known limitations of the pre-check in the browser", "## 5. Troubleshooting"),
        ("5", "## 5. Troubleshooting", "## 6. The evaluation commands"),
        ("7", "## 7. Korte handleiding voor de inkoper", "**Copilot.**"))}
    told = {
        "2.4": ("it is sure only of the one order whose export was given on the day of the cart",
                "An order the aid holds already: another export of it changes nothing",
                "Where the webshop has no such field, the aid cannot compare the cart with that order",
                "also when the cart equals the order and nothing differs: the case then has one item, this reason itself",
                'a case in which you decided an item, also "Onopgelost laten", stays here as you left it, beside the new one'),
        "2.5": ('give it to the aid, on the tab "Bestanden" (section 2.7)', "Exactly one order whose export was given today",
                '"250301-01", "250301A", "250301/2", "250301.1", "250301_01", "250301,5", "250301:2", "250301;5" and "250301+01"',
                "a number that goes on behind a letter, or behind any sign that a letter or a digit follows",
                "earlier cases of the same cart lines at the same webshop that still wait untouched",
                "The cases of a cart with the same lines at another webshop stay, and so does the case of an empty cart table.",
                "A number the aid does not know as an order counts for nothing",
                "or when a supplier's order confirmation of it came in", "make the export of the order right before you fill the cart",
                "Until you closed the case the banner repeats the first line, in the words that fit at that moment",
                "the case then has one item, the order itself, and after that the banner is green",
                "A grey case that you closed is left exactly as it is", '"Zaak afsluiten" pressed with no item decided closes nothing',
                '"Let op: kijk na of dit de goede inkooporder is", also when nothing differs',
                "**What a cart check does not compare.**", "so a green banner says nothing about those",
                'A guess on which you decided an item, also "Onopgelost laten", or that you closed, is never replaced and never changed',
                "the guess or the grey case of the cart before you typed the order's number into it"),
        "3.13": ('`"po_found_by": "cart items"`', f'`"{NOT_SURE}"`', f'`"{ALREADY_SENT}"`', "rule G01 in the audit record",
                 "exactly one stored PO that was filed today holds exactly the cart's items, and that PO is not sent",
                 "first those filed today, when there are any; then those that hold exactly the cart's items; then those that are not sent",
                 "A case that holds the page itself and stands answers at once",
                 "a press of the close button that left the case in review and carried nothing out is no act",
                 "A case without a PO that the buyer closed is left exactly as it is",
                 "whatever the buyer did on it, the rule of the paragraph above is asked again",
                 "A guess that answers so gives the first line of the kind found now",
                 "A guess on which something was done is never replaced and never changed",
                 "`lines_digest`, that of the lines alone", "finishes as replaced the cases of any day that hold the same lines digest and the same host",
                 "A cart with the same lines at another webshop is another cart, whose cases stay",
                 "a cart table without a line is matched by no lines digest, since every empty table has the same one",
                 "or a capture that opens a new case with a PO of the same lines at the same webshop (on any day and with any stated number)",
                 "A `capture.json` written before the lines digest was kept is matched by its cart digest",
                 "each side looks twice", "A replacement that was cut off at any point after the mark is finished, never undone",
                 "whose result is closed while no close of the buyer closed it is such a cut-off, not a case the buyer closed",
                 "A case whose run failed is never among them: it keeps its failed result.",
                 "or the next new case with a PO of the same lines, as on a later day",
                 "After an update of the aid, reload the extension on `chrome://extensions`",
                 "a decision, a close and an undo on it are refused", "the mark is taken back and the case is left as it is",
                 "The page of the capture that was cut off does not finish the case when that page came in other bytes than the earlier "
                 "case's own page", "the files of `cases/tryout/`, by their name alone",
                 "a request whose `Host` is 127.0.0.1 or localhost, with any port", "its close records hold the replacement",
                 "another stored PO fits its items, the cart is compared with that PO, as a guess with nothing prepared"),
        "3.17": ("It is the address the launcher starts the capture server on", "serves the page under its own address",
                 "each printed as text, so that nothing an order holds is taken for a part of the page",
                 "The case takes the supplier of its order and never the page's heading",
                 'on the tab "Bestanden" (section 2.7) or in `inbox/`', "a first price below 0,50 gives this bar",
                 "For the green walk pick an order whose first line has a price of 0,50 or more",
                 "the try-out cart of such an order gives this bar for as long as that setting is above 0",
                 "from a made-up export without a supplier column"),
        "4.9": ("A number that goes on is another number, of which no part is read",
                "A number the page states that the aid does not know as an order counts for nothing.",
                "By its items the aid is sure of an order in one case only.", "an export that changes nothing files nothing",
                "An order counts as not sent until its PDF or a supplier's confirmation of it comes in",
                "Two orders with the same items filed on one day cannot be told apart",
                "A case whose order was found keeps that order for the same cart of that day.",
                'drop the prepared change on the tab "In Exact invoeren" (section 2.6)', "One cart can be answered by two cases.",
                "A replaced case stays in its folder.",
                "the page of the capture that was cut off does not finish it when that page came in other bytes than the earlier case's own page",
                "A cart check compares the lines of the cart and nothing else of the page.",
                '"250301_01", "250301,5", "250301:2", "250301;5", "250301+01"',
                "a sign being anything that is no letter, no digit and no space",
                "when a new case with a PO of the same lines is opened at the same webshop",
                "A guess on which the buyer decided an item is never replaced",
                "has no supplier either, so with `first_cases_review` above 0 it always comes to the buyer"),
        "5": ("**The banner is grey, or amber with a guess.**", "for an order the aid holds already another export changes nothing",
              "To have the aid sure of the order, make its export right before you fill the cart.",
              "unless an item was decided in it", "also when nothing differs and the order itself is the one item"),
        "7": ("Maak de export van de inkooporder vlak voordat u de winkelwagen vult, op dezelfde dag",
              "laad de pagina na een minuut opnieuw", CHOSEN_NL.strip(), NOT_SURE_NL.partition(".")[0], SENT_NL.partition(" (")[0],
              "Kent de assistent haar al, dan helpt een nieuwe export niet", "typ daar dan het nummer van de inkooporder",
              "behalve wanneer de assistent voor een grijze winkelwagen", "waarover hij niet zeker was",
              '"Let op: kijk na of dit de goede inkooporder is"', "Hij heeft dan niets voorbereid",
              "ook als er niets verschilt: dan is de inkooporder zelf het ene punt", "Daarna is de balk groen.",
              "dan verdwijnt de eerste zaak vanzelf van de beoordelingspagina, zolang u er niets in besloten hebt",
              f'"{NOT_COMPARED["nl"]}" Die punten vergelijkt de assistent niet: kijk ze zelf na voordat u bestelt.')}
    for section, sentences in told.items():
        for sentence in sentences:
            assert sentence in sections[section], (section, sentence)
    for section in ("2.5", "4.9", "7"):
        assert f'"{NOT_COMPARED["nl"]}"' in sections[section], section
    for she in ("Zij heeft dan niets voorbereid", "waarover zij niet zeker was", "assistent zij", "Zij "):    # one word for the assistant
        assert she not in sections["7"], she
    docstring = " ".join(server_module.__doc__.split())
    for sentence in ("does not finish it when that page came in other bytes than the earlier case's own page", "(_same_lines)",
                     "digest and the same host, the webshop of the page", "or a new case with a PO of the same lines, _same_lines)",
                     "One that was cut off at any point after the mark is finished, never undone", "each side looks twice"):
        assert sentence in docstring, sentence
    readme = " ".join(Path("aid/README.md").read_text(encoding="utf-8").split())
    for sentence in ("the one order filed on the day of the cart (its export was given that day) that holds exactly the cart's items",
                     "the earlier one is finished as replaced unless the buyer decided something in it",
                     "the banner's last line says what a cart check does not compare",
                     "which the capture server serves under `/tryout/`"):
        assert sentence in readme, sentence
    extension = " ".join(Path("aid/extension/README.md").read_text(encoding="utf-8").split())
    for sentence in ("## The try-out cart", f'"{outside}"', "of whose purchase order the aid is not sure by the cart's items",
                     "the aid then checks the cart again", "computer's own host only (127.0.0.1 or localhost)",
                     "After an update of the aid, press the reload button of the extension", f'"{GUESS_TITLE}"', f'"{NOT_COMPARED["nl"]}"'):
        assert sentence in extension, sentence


# --- a write that asked the buyer for a look in Exact (a write route only) ---

@pytest.mark.parametrize("decision, verdict", [("confirm", "match"), ("keep", "differences")])
def test_a_cart_whose_look_the_buyer_confirmed_reads_as_settled(capture_server, madeup_out, decision, verdict):
    """The check's proof: a cart whose small price difference a route wrote and could not verify. Before the buyer decides, the
    line is a difference with the label of the look. After "Bevestigen" and the close the write counts as made, for the review
    page and for the banner alike: the same cart reads as a match. After "Houden" it stays a difference."""
    from aid.execution.writer import PreparedWriter, RetryingWriter, WriteLog
    from aid.review.app import _saved_result, decide
    from aid.review.i18n import STRINGS
    from aid.review.page import close_text
    from aid.tests.test_review import Unchecked

    prepared = capture_server.settings.cases_root / "prepared"
    page_deps = capture_server.deps.model_copy(update={"writer": PreparedWriter(prepared)})
    capture_server.server.deps = capture_server.deps.model_copy(update={"writer": RetryingWriter(Unchecked("po_line_price", read="4.12"), PreparedWriter(prepared))})
    html = page(madeup_out, "CART01.html", texts={"€ 4,10": "€ 4,15"})
    first = post(capture_server, html)[1]
    case = load_case(capture_server.settings.cases_root, first["case_id"])
    assert (first["status"], first["verdict"]) == ("review", "differences")
    assert [(e.op.kind, e.status, e.previous, e.error) for e in WriteLog(case.root).entries()] == [("po_line_price", "done", "4.12", "verify: no answer")]
    for item in _saved_result(case).review_items:
        decide(case, item.item_id, decision)
    assert close_text(case, page_deps, "nl") == ("De zaak is nu: afgesloten.", False)
    again = post(capture_server, html)[1]
    assert (again["case_id"], again["status"], again["verdict"]) == (first["case_id"], "closed", verdict)
    assert len([e for e in WriteLog(case.root).entries() if e.undo_of is None]) == 1          # the close wrote the change no second time
    [price] = [line for line in again["lines"] if line["field"] == "price" and line["line_id"] == "2"]
    if decision == "keep":                             # the look he did not confirm never counts as made: the line stays the difference
        assert again["message"] == "Inkooporder 250201: 2 regels komen overeen en 1 verschil"
        assert (price["kind"], price["reason"]) == ("flag", STRINGS["nl"]["reason:write made, not verified: check in Exact"])
        assert price["text"].endswith("besluit van de inkoper: waarde van de inkooporder gehouden")
    else:
        assert "verschil" not in again["message"] and "terugzetten" not in price["text"]
