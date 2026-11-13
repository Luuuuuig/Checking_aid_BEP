"""Tests of the digest (aid/notify.py): its lines and links, its blocks in the two languages, the HTML, when it is due and how it
is sent."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email import message_from_bytes, policy
from html import escape
from typing import get_args

import pytest

from aid.comparison import Field, Findings, Flag, Uncompared
from aid.execution.ops import OpKind, WriteOp
from aid.execution.writer import WriteLogEntry
from aid.messaging import QUESTIONS, FileMailer
from aid.notify import build_digest, digest_due, list_link, review_link, send_digest, undo_link
from aid.pipeline import NOT_APPLIED, ReviewItem, _write_name     # _write_name: how the pipeline names a write in a reason or an error
from aid.schema import DocValue
from aid.tests.conftest import SETTINGS
from aid.tests.fakes import case_result, entry


def test_digest_lines_and_links():
    closed = case_result("250101_20261118T090000", "closed", writes=[entry("po_line_price", "12.50", "12.70", entry_id="e1")])
    review = case_result("250107_20261118T091000", "review")
    d = build_digest([closed, review], [("again.pdf", "duplicate")], SETTINGS, date(2026, 11, 18))
    assert d.subject == "[Inkoopassistent] Dagoverzicht 18-11-2026" and "12,50 → 12,70" in d.text and undo_link(SETTINGS, closed.case_id, "e1") in d.text
    assert review_link(SETTINGS, review.case_id) in d.text and "duplicate" in d.text
    assert "ongedaan" not in build_digest([review], [], SETTINGS, date(2026, 11, 18)).text

def test_digest_due():
    assert digest_due(datetime(2026, 11, 18, 16, 31), None, SETTINGS) and not digest_due(datetime(2026, 11, 18, 16, 0), None, SETTINGS)
    assert not digest_due(datetime(2026, 11, 18, 17, 0), datetime(2026, 11, 18, 16, 31), SETTINGS)


# The tests below cover what the rulings of the task add to the two above.

ENGLISH = SETTINGS.model_copy(update={"language": "en"})
DAY = date(2026, 11, 18)
PRICE = Flag(flag_id="F1", field="price", po_line_ids=["1"], doc_line_ids=["1"], po_value=DocValue(value="12.50"),
             doc_value=DocValue(value="14.00"), detail="different")


def headings(text: str) -> list[str]:
    """The headings of the blocks of a digest text: the lines that follow a blank line."""
    lines = text.splitlines()
    return [line for before, line in zip(lines, lines[1:]) if before == ""]


def block(text: str, heading: str) -> list[str]:
    """The lines of the block of a digest text under this heading, the heading first, up to the blank line that ends it."""
    lines = text.splitlines()
    rest = lines[lines.index(heading):]
    return rest[:rest.index("")] if "" in rest else rest


def changed(made: WriteLogEntry, **update) -> WriteLogEntry:
    """The entry with its operation changed as given: the attach_document and the set_status of a case name no line."""
    return made.model_copy(update={"op": made.op.model_copy(update=update)})


def item(item_id: str, reason: str, finding_id: str | None = None, field: str | None = None) -> ReviewItem:
    """A review item with this reason, about the finding with this id and its field when they are given."""
    return ReviewItem(item_id=item_id, finding_id=finding_id, reason=reason, field=field, po_value=None, doc_value=None)


def question(template: str, rule_id: str = "R06") -> dict:
    """The record of a message sent, as a case result keeps it."""
    return {"rule_id": rule_id, "template": template, "items": [], "finding_ids": ["F1"], "status": "sent", "timestamp": "2026-11-18T09:30:00"}


def test_dutch_writes():
    """A line per write under its case: the field in words, the line, the article or the file, old → new with the decimal comma
    for a price or a quantity, and the undo link of a write made; a prepared change and a failed write have no link."""
    writes = [entry("po_line_price", "12.50", "12.70", entry_id="e1"), entry("po_line_quantity", "2.5", "3", entry_id="e2"),
              entry("po_line_date", "2026-11-16", "2026-W48", entry_id="e3"),
              entry("article_price", None, "12.70", entry_id="e4").model_copy(update={"status": "prepared"}),
              changed(entry("set_status", "Besteld", "Bevestigd", entry_id="e5"), line_id=None).model_copy(update={"status": "failed"}),
              changed(entry("attach_document", None, None, entry_id="e6"), line_id=None,
                      document="/drive/BEP-aid/cases/250101_20261118T090000/input/MU01_conf.pdf")]
    closed = case_result("250101_20261118T090000", "closed", writes=writes, supplier="S01")
    d = build_digest([closed], [], SETTINGS, DAY)
    links = {entry_id: undo_link(SETTINGS, closed.case_id, entry_id) for entry_id in ("e1", "e2", "e3", "e6")}
    assert block(d.text, "Behandelde zaken") == [
        "Behandelde zaken", f"- {closed.digest_line}",
        f"    - prijs regel 1: 12,50 → 12,70 [ongedaan maken]({links['e1']})",
        f"    - aantal regel 1: 2,5 → 3 [ongedaan maken]({links['e2']})",
        f"    - levertijd regel 1: 2026-11-16 → 2026-W48 [ongedaan maken]({links['e3']})",
        "    - artikelprijs KM-1001: onbekend → 12,70 (voorbereid)",
        "    - status: Besteld → Bevestigd (mislukt)",
        f"    - bijlage MU01_conf.pdf [ongedaan maken]({links['e6']})"]
    assert "undo=e4" not in d.text + d.html and "undo=e5" not in d.text + d.html


def test_english_digest():
    """The English digest: its subject and headings, the words of its lines and the decimal point."""
    writes = [entry("po_line_price", "1234.50", "1240.00", entry_id="e1"), entry("po_line_quantity", "2.5", "3", entry_id="e2"),
              entry("article_price", "12.50", "12.70", entry_id="e3").model_copy(update={"status": "prepared"}),
              entry("po_line_date", "2026-11-16", "2026-11-20", entry_id="e4").model_copy(update={"status": "failed"})]
    closed = case_result("250101_20261118T090000", "closed", writes=writes, supplier="S01", language="en")
    review = case_result("250107_20261118T091000", "review", supplier="S07", language="en").model_copy(
        update={"review_items": [item("RI1", "new supplier")]})
    waiting = case_result("250106_20261118T093000", "waiting", supplier="S06", language="en").model_copy(
        update={"messages": [question("line_missing_extra", "R07")]})
    failed = case_result("unknown_20261118T100000", "failed", language="en").model_copy(update={"error": "ReaderError: no template for scan.pdf"})
    d = build_digest([closed, review, waiting, failed], [("again.pdf", "duplicate")], ENGLISH, DAY)
    assert d.subject == "[Inkoopassistent] Daily digest 18-11-2026" and d.period == "2026-11-18" and d.text.startswith("Daily digest 18-11-2026\n")
    assert headings(d.text) == ["Handled cases", "Waiting for you", "Question sent to the supplier", "Failed", "Skipped"]
    assert block(d.text, "Handled cases")[1:3] == [f"- {closed.digest_line}", f"    - price line 1: 1234.50 → 1240.00 [undo]({undo_link(ENGLISH, closed.case_id, 'e1')})"]
    assert block(d.text, "Handled cases")[3:6] == [f"    - quantity line 1: 2.5 → 3 [undo]({undo_link(ENGLISH, closed.case_id, 'e2')})",
                                                    "    - article price KM-1001: 12.50 → 12.70 (prepared)",
                                                    "    - delivery date line 1: 2026-11-16 → 2026-11-20 (failed)"]
    assert block(d.text, "Waiting for you")[1:] == [f"- PO 250107 (S07): [review]({review_link(ENGLISH, review.case_id)})", "    - New supplier"]
    assert block(d.text, "Question sent to the supplier")[1:] == ["- PO 250106 (S06): missing or extra line"]
    assert block(d.text, "Failed")[1:] == ["- PO unknown: ReaderError: no template for scan.pdf"]
    assert block(d.text, "Skipped")[1:] == ["- again.pdf: duplicate"]
    assert "ongedaan" not in d.text and "regel" not in d.text and "Dagoverzicht" not in d.text + d.html and '<html lang="en">' in d.html


def test_review_block_lists_the_items_of_each_case():
    """Each case in review with its link to the review page and a line per review item: the reason by the review page's label, then
    the field and the line when they are known. No rule code is shown: the write that the pipeline names by its rule is part of a
    reason the label replaces."""
    extra = Flag(flag_id="F2", field="line_presence", po_line_ids=[], doc_line_ids=["4"], po_value=None, doc_value=DocValue(value="KM-1709"),
                 detail="extra line")
    merged = Uncompared(field="unit", po_line_ids=["2", "3"], doc_line_ids=["2"], reason="merged line")
    article = WriteOp(kind="article_price", po_number="250107", version=1, article_code="KM-1701", new_value="14.00")
    items = [item("RI1", "price above threshold", "F1", "price"), item("RI2", "messages off for this supplier", "F2", "line_presence"),
             item("RI3", "merged line", "U1", "unit"), item("RI4", f"{NOT_APPLIED} ({_write_name('R04', article)})", "F1", "price"),
             item("RI5", "no answer in 3 working days")]
    review = case_result("250107_20261118T091000", "review", supplier="S07").model_copy(update={
        "findings": Findings(version=1, flags=[PRICE, extra], matches=[], uncompared=[merged]), "review_items": items})
    unread = case_result("unknown_20261118T094500", "review").model_copy(update={"review_items": [item("RI1", "no version selected")]})
    d = build_digest([review, unread], [], SETTINGS, DAY)
    assert block(d.text, "Wacht op u") == [
        "Wacht op u", f"- PO 250107 (S07): [beoordelen]({review_link(SETTINGS, review.case_id)})",
        "    - Prijsverschil boven de drempel (prijs, regel 1)",
        "    - De assistent stuurt deze leverancier uit zichzelf geen vragen (ontbrekende of extra regel, regel 4 van het document)",
        "    - Samengevoegde regel (eenheid, regels 2, 3)",
        "    - Niet uitgevoerd: een eerdere schrijfactie is mislukt (prijs, regel 1)",
        "    - Geen antwoord binnen de wachttijd",
        f"- PO onbekend: [beoordelen]({review_link(SETTINGS, unread.case_id)})", "    - Geen versie van de inkooporder gekozen"]
    assert "R04" not in d.text + d.html and "article_price" not in d.text + d.html


def test_waiting_block_names_the_questions_and_the_supplier():
    """Each case that waits for the supplier, with the supplier and the template of each question in words; the request to approve
    a PO again is no question."""
    waiting = case_result("250106_20261118T093000", "waiting", supplier="S06").model_copy(
        update={"messages": [question("quantity_unit"), question("missing_info", "R08"), question("reapproval", "R10")]})
    asked = case_result("250108_20261118T094000", "waiting", supplier="S08").model_copy(update={"messages": [question("line_missing_extra", "R07")]})
    d = build_digest([waiting, asked], [], SETTINGS, DAY)
    assert block(d.text, "Vraag gesteld aan de leverancier") == [
        "Vraag gesteld aan de leverancier", "- PO 250106 (S06): aantal of eenheid, ontbrekende gegevens", "- PO 250108 (S08): ontbrekende of extra regel"]
    assert "reapproval" not in d.text and "R06" not in d.text + d.html


def test_failed_block_gives_the_first_line_of_the_error():
    """Each failed case with the first line of its error, without the write that the pipeline names by its rule there."""
    unread = case_result("unknown_20261118T100000", "failed").model_copy(update={"error": "ReaderError: no template for scan.pdf\nmore detail"})
    op = WriteOp(kind="po_line_price", po_number="250101", version=1, line_id="1", old_value="12.50", new_value="12.70")
    unwritten = case_result("250101_20261118T101000", "failed", supplier="S01").model_copy(
        update={"error": f"WriterError: cannot write prepared_changes.csv (write: {_write_name('R04', op)})"})
    d = build_digest([unread, unwritten], [], SETTINGS, DAY)
    assert block(d.text, "Mislukt") == ["Mislukt", "- PO onbekend: ReaderError: no template for scan.pdf",
                                        "- PO 250101 (S01): WriterError: cannot write prepared_changes.csv"]
    assert "more detail" not in d.text + d.html and "R04" not in d.text + d.html


def test_blocks_in_order_each_left_out_when_empty():
    """The blocks come in their order, each only when it has a line; the skipped files are grouped by their reason; the digest shows
    no rule code and never the authorization amount; a day with nothing says so."""
    limited = SETTINGS.model_copy(update={"authorization_limit": Decimal("98765.43")})      # made up, and unlike any other number here
    closed = case_result("250101_20261118T090000", "closed", writes=[entry("po_line_price", "12.50", "12.70", entry_id="e1")])
    review = case_result("250107_20261118T091000", "review").model_copy(update={"review_items": [item("RI1", "new supplier")]})
    waiting = case_result("250106_20261118T093000", "waiting").model_copy(update={"messages": [question("quantity_unit")]})
    failed = case_result("250102_20261118T100000", "failed").model_copy(update={"error": "ValueError: case 250102 has no document to read"})
    skipped = [("invoice.pdf", "invoice"), ("again.pdf", "duplicate"), ("slip.pdf", "packing_slip"), ("bill.pdf", "invoice")]
    d = build_digest([closed, review, waiting, failed], skipped, limited, DAY)
    assert headings(d.text) == ["Behandelde zaken", "Wacht op u", "Vraag gesteld aan de leverancier", "Mislukt", "Overgeslagen"]
    assert block(d.text, "Overgeslagen")[1:] == ["- again.pdf: duplicate", "- invoice.pdf: invoice", "- bill.pdf: invoice", "- slip.pdf: packing_slip"]
    assert "R04" not in d.text + d.html and "R06" not in d.text + d.html and "98765" not in d.text + d.html
    assert headings(build_digest([closed], [], SETTINGS, DAY).text) == ["Behandelde zaken"]
    assert headings(build_digest([], skipped, SETTINGS, DAY).text) == ["Overgeslagen"]
    empty = build_digest([], [], SETTINGS, DAY)
    assert empty.text == "Dagoverzicht 18-11-2026\n\nEr zijn geen zaken behandeld en geen bestanden overgeslagen.\n"
    assert "<p>Er zijn geen zaken behandeld en geen bestanden overgeslagen.</p>" in empty.html and "<h2>" not in empty.html


def test_html_has_the_same_content_with_every_value_escaped():
    """The HTML holds the content of the text as headings, lists and anchors, each value passed through html.escape."""
    closed = case_result("250101_20261118T090000", "closed", writes=[entry("po_line_price", "12.50", "12.70", entry_id="e1")], supplier="S<1> & Co")
    review = case_result("250107_20261118T091000", "review").model_copy(update={"review_items": [item("RI1", "price <above> & beyond")]})
    d = build_digest([closed, review], [("a<b>&c.pdf", "duplicate")], SETTINGS, DAY)
    assert "- PO 250101 (S<1> & Co): afgesloten, 1 schrijfactie" in d.text.splitlines() and "- a<b>&c.pdf: duplicate" in d.text.splitlines()
    assert "<h1>Dagoverzicht 18-11-2026</h1>" in d.html and all(f"<h2>{heading}</h2>" in d.html for heading in ("Behandelde zaken", "Wacht op u", "Overgeslagen"))
    assert "<li>PO 250101 (S&lt;1&gt; &amp; Co): afgesloten, 1 schrijfactie" in d.html and "<li>a&lt;b&gt;&amp;c.pdf: duplicate</li>" in d.html
    assert "<li>price &lt;above&gt; &amp; beyond</li>" in d.html and "<1>" not in d.html and "<b>" not in d.html and "<above>" not in d.html
    undo, review_page = escape(undo_link(SETTINGS, closed.case_id, "e1")), escape(review_link(SETTINGS, review.case_id))
    assert "&amp;undo=e1" in undo and f'<li>prijs regel 1: 12,50 → 12,70 <a href="{undo}">ongedaan maken</a></li>' in d.html
    assert f'<li>PO 250107: <a href="{review_page}">beoordelen</a>' in d.html and '<html lang="nl">' in d.html and '<meta charset="utf-8">' in d.html


def test_every_kind_field_and_question_is_in_words_in_both_languages():
    """Each kind of write, each field of a review item and each question has its words in both languages, so that no case stops the
    digest and none is shown as its code."""
    codes = [code for code in (*get_args(OpKind), *get_args(Field), *QUESTIONS) if "_" in code]     # "price" is a code and a word
    for settings in (SETTINGS, ENGLISH):
        writes = [entry(kind, "1.5", "2.5", entry_id=f"e{n}") for n, kind in enumerate(get_args(OpKind))]
        closed = case_result("250101_20261118T090000", "closed", writes=writes, language=settings.language)
        review = case_result("250107_20261118T091000", "review", language=settings.language).model_copy(
            update={"review_items": [item(f"RI{n}", "price above threshold", field=field) for n, field in enumerate(get_args(Field), 1)]})
        waiting = case_result("250106_20261118T093000", "waiting", language=settings.language).model_copy(
            update={"messages": [question(template) for template in QUESTIONS]})
        d = build_digest([closed, review, waiting], [], settings, DAY)
        assert [code for code in codes if code in d.text + d.html] == []


def test_send_digest_writes_one_eml_to_the_buyer(tmp_path):
    """send_digest sends the text to the buyer alone, under the digest's subject, through the mailer; the file mailer writes one .eml."""
    d = build_digest([case_result("250101_20261118T090000", "closed", writes=[entry("po_line_price", "12.50", "12.70", entry_id="e1")])], [],
                     SETTINGS, DAY)
    rec = send_digest(d, FileMailer(tmp_path, sender=SETTINGS.aid_email, clock=lambda: datetime(2026, 11, 18, 16, 31)), SETTINGS)
    [eml] = tmp_path.glob("*.eml")
    mail = message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert eml.name == f"{rec.message_id}.eml" and mail["From"] == SETTINGS.aid_email and mail["To"] == SETTINGS.buyer_email and mail["Cc"] is None
    assert mail["Subject"] == d.subject and mail["X-Aid-Template"] == "digest" and mail["X-Aid-Case"] == "digest-2026-11-18"
    assert mail.get_content() == d.text and rec.to == [SETTINGS.buyer_email] and rec.subject == d.subject and rec.template == "digest"


def test_links_leave_out_a_trailing_slash_of_the_review_url():
    """A review_url with a trailing slash is used without it, so that a link never holds "//?case="."""
    slashed = SETTINGS.model_copy(update={"review_url": "https://review.example.com/aid/"})
    assert undo_link(slashed, "250101_20261118T090000", "e1") == "https://review.example.com/aid/?case=250101_20261118T090000&undo=e1"
    assert review_link(slashed, "250101_20261118T090000") == "https://review.example.com/aid/?case=250101_20261118T090000"
    assert review_link(SETTINGS, "250101_20261118T090000") == "http://localhost:7860/?case=250101_20261118T090000"


def test_digest_due_from_the_minute_once_a_day_or_per_case():
    """Daily, the digest is due from settings.digest_time on and once on each date, with a time sent in another zone taken on
    now's clock; per case it is always due."""
    assert digest_due(datetime(2026, 11, 18, 16, 30), datetime(2026, 11, 17, 16, 31), SETTINGS)       # at the minute, sent the day before
    assert not digest_due(datetime(2026, 11, 18, 16, 29, 59), datetime(2026, 11, 17, 16, 31), SETTINGS)
    pacific = timezone(timedelta(hours=-8))
    sent = datetime(2026, 11, 19, 0, 31, tzinfo=timezone.utc)                                         # 16:31 on 18 November at -08:00
    assert digest_due(datetime(2026, 11, 19, 16, 31, tzinfo=pacific), sent, SETTINGS)
    assert not digest_due(datetime(2026, 11, 18, 16, 45, tzinfo=pacific), sent, SETTINGS)
    per_case = SETTINGS.model_copy(update={"digest": "per_case"})
    assert digest_due(datetime(2026, 11, 18, 9, 0), datetime(2026, 11, 18, 8, 59), per_case) and digest_due(datetime(2026, 11, 18, 9, 0), None, per_case)


# Fix round 1 of the review.

def test_a_parenthesis_without_a_rule_code_is_kept():
    """Only the pipeline's name of a write, which holds a rule code, is left out of an error; a writer's own parenthesis stays. A
    review item's reason that has a label is shown by the label, as on the review page; one without a label keeps its parenthesis."""
    review = case_result("250107_20261118T091000", "review").model_copy(update={"review_items": [
        item("RI1", "write failed: Exact said no (rule violation)"), item("RI2", "held by the buyer (rule violation)")]})
    failed = case_result("250101_20261118T101000", "failed").model_copy(update={"error": "WriterError: Exact said no (rule violation)"})
    d = build_digest([review, failed], [], SETTINGS, DAY)
    assert block(d.text, "Wacht op u")[2:] == ["    - Schrijfactie mislukt", "    - held by the buyer (rule violation)"]
    assert "- PO 250101: WriterError: Exact said no (rule violation)" in d.text.splitlines()


def test_a_malformed_result_does_not_stop_the_digest():
    """A saved message record without a template and a word the vocabulary lacks are shown as they are, not a KeyError."""
    waiting = case_result("250106_20261118T093000", "waiting", supplier="S06").model_copy(
        update={"messages": [{"rule_id": "R06", "status": "sent"}, question("quantity_unit")]})
    odd = item("RI1", "price above threshold", field="price").model_copy(update={"field": "colour"})     # model_copy does not validate
    review = case_result("250107_20261118T091000", "review").model_copy(update={"review_items": [odd]})
    d = build_digest([waiting, review], [], SETTINGS, DAY)
    assert block(d.text, "Vraag gesteld aan de leverancier")[1:] == ["- PO 250106 (S06): aantal of eenheid"]
    assert "    - Prijsverschil boven de drempel (colour)" in d.text.splitlines()


def test_english_review_items_and_the_article_before_the_line():
    """The English words of a review item: the field, "lines" and "of the document"; a write with an article and a line names the
    article."""
    extra = Flag(flag_id="F2", field="line_presence", po_line_ids=[], doc_line_ids=["4"], po_value=None, doc_value=DocValue(value="KM-1709"),
                 detail="extra line")
    merged = Uncompared(field="delivery_date", po_line_ids=["2", "3"], doc_line_ids=["2"], reason="merged line")
    review = case_result("250107_20261118T091000", "review", supplier="S07", language="en").model_copy(update={
        "findings": Findings(version=1, flags=[PRICE, extra], matches=[], uncompared=[merged]),
        "review_items": [item("RI1", "price above threshold", "F1", "price"), item("RI2", "messages off for this supplier", "F2", "line_presence"),
                         item("RI3", "merged line", "U1", "delivery_date")]})
    both = changed(entry("article_price", "12.50", "12.70", entry_id="e1"), line_id="1")
    closed = case_result("250101_20261118T090000", "closed", writes=[both], language="en")
    d = build_digest([closed, review], [], ENGLISH, DAY)
    assert block(d.text, "Waiting for you")[2:] == ["    - Price difference above the threshold (price, line 1)",
                                                    "    - The aid sends this supplier no questions by itself (missing or extra line, line 4 of the document)",
                                                    "    - Merged line (delivery date, lines 2, 3)"]
    assert f"    - article price KM-1001: 12.50 → 12.70 [undo]({undo_link(ENGLISH, closed.case_id, 'e1')})" in d.text.splitlines()


# --- the final review, group A ---

def test_review_reasons_are_shown_by_the_labels_of_the_review_page():
    """The reason of a review item reads as the review page shows it, in the digest's language: the label of the reason, or of the
    reason it begins with; a reason without a label is shown as it is."""
    from aid.review.i18n import STRINGS

    items = [item("RI1", "price above threshold", "F1", "price"), item("RI2", "no answer in 3 working days"),
             item("RI3", "a reason the page does not know")]
    for settings, language in ((SETTINGS, "nl"), (ENGLISH, "en")):
        review = case_result("250107_20261118T091000", "review", supplier="S07", language=language).model_copy(update={
            "findings": Findings(version=1, flags=[PRICE], matches=[], uncompared=[]), "review_items": items})
        d = build_digest([review], [], settings, DAY)
        strings = STRINGS[language]
        assert d.text.splitlines()[-3:] == [f"    - {strings['reason:price above threshold']} ({'prijs, regel' if language == 'nl' else 'price, line'} 1)",
                                            f"    - {strings['reason:no answer in *']}", "    - a reason the page does not know"]
    assert "Prijsverschil boven de drempel (prijs, regel 1)" in build_digest([case_result("250107_20261118T091000", "review").model_copy(
        update={"findings": Findings(version=1, flags=[PRICE], matches=[], uncompared=[]), "review_items": items[:1]})], [], SETTINGS, DAY).text


def test_the_undo_link_selects_its_case_and_write_on_the_review_page(tmp_path, madeup_out):
    """The query of the digest's undo link, read as the review page reads it, selects the case and the write; the review link selects
    the case alone."""
    from urllib.parse import parse_qsl, urlsplit

    from aid.pipeline import run_case
    from aid.review.page import selection_from_query
    from aid.sources.upload import open_upload_case
    from aid.tests.conftest import deps

    class Made:
        """A write route that makes every change in the system (status "done")."""
        name = "made"

        def apply(self, op, case_id, rule_id, evidence, now):
            return WriteLogEntry(entry_id=f"e{op.kind}", case_id=case_id, op=op, status="done", previous=op.old_value, new=op.new_value,
                                 rule_id=rule_id, evidence=evidence, timestamp=now.isoformat())

    d = deps(tmp_path, madeup_out, writer=Made())
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU01_conf.pdf"], "250101", "confirmation", d.clock())
    result = run_case(c, d)
    entry_id = result.write_entries[0].entry_id
    query = dict(parse_qsl(urlsplit(undo_link(d.settings, c.case_id, entry_id)).query))
    assert selection_from_query(d.settings.cases_root, query) == (c.case_id, entry_id)
    query = dict(parse_qsl(urlsplit(review_link(d.settings, c.case_id)).query))
    assert selection_from_query(d.settings.cases_root, query) == (c.case_id, None)


@pytest.mark.parametrize("language, line", [
    ("nl", "Postvak niet verbonden: meld u opnieuw aan op het tabblad Instellingen, onder Postvak en AI"),
    ("en", "Mailbox not connected: sign in again on the Settings tab, under Mailbox and AI")])
def test_a_lapsed_mailbox_is_one_line_under_the_title_before_the_blocks(language, line):
    settings = SETTINGS.model_copy(update={"language": language})
    lapsed = build_digest([case_result("250101_20261118T090000", "closed")], [], settings, date(2026, 11, 18), mailbox_lapsed=True)
    assert lapsed.text.splitlines()[1:3] == ["", line] and lapsed.text.count(line) == 1
    assert lapsed.html.index("</h1>") < lapsed.html.index(f"<p>{line}</p>") < lapsed.html.index("<h2>")
    assert line not in build_digest([], [], settings, date(2026, 11, 18)).text


@pytest.mark.parametrize("language, waiting, line", [
    ("nl", 2, "Copilot werkt niet (Copilot refused: 403 Forbidden): 2 zaken wachten erop; meld u aan voor Copilot of test Copilot op "
              "het tabblad Instellingen, onder Postvak en AI"),
    ("nl", 1, "Copilot werkt niet (Copilot refused: 403 Forbidden): 1 zaak wacht erop; meld u aan voor Copilot of test Copilot op "
              "het tabblad Instellingen, onder Postvak en AI"),
    ("en", 3, "Copilot is not working (Copilot refused: 403 Forbidden): 3 cases wait for it; sign in for Copilot or test Copilot on "
              "the Settings tab, under Mailbox and AI")])
def test_copilot_that_fails_is_one_line_under_the_title_with_the_cases_waiting(language, waiting, line):
    settings = SETTINGS.model_copy(update={"language": language})
    d = build_digest([], [], settings, date(2026, 11, 18), mailbox_lapsed=True, copilot_down=("Copilot refused: 403 Forbidden", waiting))
    assert d.text.splitlines()[2:4][-1] == line and d.text.count(line) == 1
    assert d.html.index("</h1>") < d.html.index(f"<p>{escape(line)}</p>")
    assert "Copilot" not in build_digest([], [], settings, date(2026, 11, 18)).text
    marked = build_digest([], [], settings, date(2026, 11, 18), copilot_down=("Copilot ended the conversation: <b>", 1)).html
    assert "<b>" not in marked and "&lt;b&gt;" in marked


@pytest.mark.parametrize("language, refused, line", [
    ("nl", 1, "Copilot kon 1 document niet lezen; nieuwe poging bij de volgende ronde"),
    ("nl", 2, "Copilot kon 2 documenten niet lezen; nieuwe poging bij de volgende ronde"),
    ("en", 1, "Copilot could not read 1 document; it is tried again at the next pass"),
    ("en", 3, "Copilot could not read 3 documents; they are tried again at the next pass")])
def test_the_documents_copilot_could_not_read_are_a_line_of_their_own(language, refused, line):
    settings = SETTINGS.model_copy(update={"language": language})
    d = build_digest([], [], settings, date(2026, 11, 18), copilot_refused=refused)
    assert d.text.splitlines()[2] == line and "Copilot werkt niet" not in d.text and "not working" not in d.text
    both = build_digest([], [], settings, date(2026, 11, 18), copilot_down=("Copilot refused: 403 Forbidden", 2), copilot_refused=refused)
    assert both.text.splitlines()[3] == line
    assert "Copilot" not in build_digest([], [], settings, date(2026, 11, 18), copilot_refused=0).text


# --- the line of the changes to enter in Exact (Task 34) ---

def test_the_digest_names_the_changes_to_enter_in_one_line():
    """While changes are open on the list 'In Exact invoeren', one line under the title gives their number and the link to the tab,
    before the other lines under the title and before the blocks; the link opens the review page with the tab selected."""
    link = list_link(SETTINGS)
    assert link == "http://localhost:7860/?tab=invoeren"
    assert list_link(SETTINGS.model_copy(update={"review_url": "https://review.example.com/aid/"})) == "https://review.example.com/aid/?tab=invoeren"
    prepared = entry("po_line_price", "12.50", "12.70", entry_id="e1").model_copy(update={"status": "prepared"})
    closed = case_result("250101_20261118T090000", "closed", writes=[prepared])
    for settings, to_enter, line in ((SETTINGS, 1, f"In Exact in te voeren: 1 wijziging. Open het tabblad In Exact invoeren: {link}"),
                                     (SETTINGS, 3, f"In Exact in te voeren: 3 wijzigingen. Open het tabblad In Exact invoeren: {link}"),
                                     (ENGLISH, 1, f"To enter in Exact: 1 change. Open the tab Enter in Exact: {link}"),
                                     (ENGLISH, 2, f"To enter in Exact: 2 changes. Open the tab Enter in Exact: {link}")):
        d = build_digest([closed], [], settings, DAY, to_enter=to_enter)
        assert d.text.splitlines()[1:4] == ["", line, ""] and d.text.count(link) == 1, line
        words = line.removesuffix(link)
        assert f'<p>{escape(words)}<a href="{escape(link)}">{escape(link)}</a></p>' in d.html
        assert d.html.index("</h1>") < d.html.index(escape(words)) < d.html.index("<h2>")
    both = build_digest([closed], [], SETTINGS, DAY, mailbox_lapsed=True, copilot_down=("Copilot refused: 403 Forbidden", 1), copilot_refused=1,
                        to_enter=2)
    lines = both.text.splitlines()
    assert [line.split(":")[0] for line in lines[2:6]] == ["In Exact in te voeren", "Postvak niet verbonden", "Copilot werkt niet (Copilot refused",
                                                           "Copilot kon 1 document niet lezen; nieuwe poging bij de volgende ronde"]
    assert lines[6:8] == ["", "Behandelde zaken"]
    empty = build_digest([], [], SETTINGS, DAY, to_enter=1)                        # a day without cases, with a change still open
    assert empty.text == (f"Dagoverzicht 18-11-2026\n\nIn Exact in te voeren: 1 wijziging. Open het tabblad In Exact invoeren: {link}\n\n"
                          "Er zijn geen zaken behandeld en geen bestanden overgeslagen.\n")
    limited = SETTINGS.model_copy(update={"authorization_limit": Decimal("98765.43")})
    shown = build_digest([closed], [], limited, DAY, to_enter=2)
    assert "98765" not in shown.text + shown.html


def test_a_digest_without_the_count_is_unchanged():
    """A digest built without the count, or with none open, is the digest of before: no line of the list, no link to its tab. The
    texts of the tests above pin what that digest is."""
    closed = case_result("250101_20261118T090000", "closed", writes=[entry("po_line_price", "12.50", "12.70", entry_id="e1")])
    review = case_result("250107_20261118T091000", "review").model_copy(update={"review_items": [item("RI1", "new supplier")]})
    for results, skipped, settings, notes in (([closed, review], [("again.pdf", "duplicate")], SETTINGS, {}), ([], [], ENGLISH, {}),
                                              ([closed], [], SETTINGS, {"mailbox_lapsed": True, "copilot_down": ("Copilot refused: 403", 2)})):
        before = build_digest(results, skipped, settings, DAY, **notes)
        assert build_digest(results, skipped, settings, DAY, **notes, to_enter=0) == before
        assert "tab=invoeren" not in before.text + before.html and "Exact" not in before.text + before.html
    assert build_digest([], [], SETTINGS, DAY).text == "Dagoverzicht 18-11-2026\n\nEr zijn geen zaken behandeld en geen bestanden overgeslagen.\n"


# --- the review round of Task 34: a list that could not be read (Z4), and what the buyer does in Exact himself (Z5) ---

def test_a_list_that_could_not_be_read_is_said_in_one_line_with_the_link():
    """When the list could not be read (to_enter None), the line under the title says so, with the link to the tab, instead of
    looking like a day with nothing to enter."""
    link = list_link(SETTINGS)
    for settings, line in ((SETTINGS, f"De lijst In Exact invoeren kon niet worden gelezen. Open het tabblad om te zien wat er nog in te voeren is: {link}"),
                           (ENGLISH, f"The list Enter in Exact could not be read. Open the tab to see what is still to enter: {link}")):
        d = build_digest([], [], settings, DAY, to_enter=None)
        assert d.text.splitlines()[1:4] == ["", line, ""] and d.text.count(link) == 1
        assert f'<a href="{escape(link)}">{escape(link)}</a></p>' in d.html
    assert "kon niet worden gelezen" not in build_digest([], [], SETTINGS, DAY, to_enter=0).text


def test_the_attachment_and_the_status_prepared_are_for_the_buyer_to_do_in_exact():
    """The aid prepares the confirmation to attach and the status Bevestigd too, but lists them nowhere: their lines in the digest say
    that the buyer does them in Exact himself, and the case's line counts them apart from the prepared changes, which the tab lists. A
    write made, and one that failed, keep their words."""
    def prepared(made: WriteLogEntry) -> WriteLogEntry:
        return made.model_copy(update={"status": "prepared"})
    attach = changed(entry("attach_document", None, None, entry_id="e3"), line_id=None, document="/cases/250101_20261118T090000/input/MU01_conf.pdf")
    status = changed(entry("set_status", "Besteld", "Bevestigd", entry_id="e4"), line_id=None)
    writes = [prepared(entry("po_line_price", "12.50", "12.70", entry_id="e1")), prepared(entry("article_price", None, "12.70", entry_id="e2")),
              prepared(attach), prepared(status)]
    for settings, heading, lines in ((SETTINGS, "Behandelde zaken", ["- PO 250101 (S01): afgesloten, 2 voorbereide wijzigingen, 2 in Exact zelf te doen",
                                        "    - prijs regel 1: 12,50 → 12,70 (voorbereid)", "    - artikelprijs KM-1001: onbekend → 12,70 (voorbereid)",
                                        "    - bijlage MU01_conf.pdf (zelf in Exact doen)", "    - status: Besteld → Bevestigd (zelf in Exact doen)"]),
                            (ENGLISH, "Handled cases", ["- PO 250101 (S01): closed, 2 prepared changes, 2 to do in Exact yourself",
                                       "    - price line 1: 12.50 → 12.70 (prepared)", "    - article price KM-1001: unknown → 12.70 (prepared)",
                                       "    - attachment MU01_conf.pdf (do it in Exact yourself)",
                                       "    - status: Besteld → Bevestigd (do it in Exact yourself)"])):
        closed = case_result("250101_20261118T090000", "closed", writes=writes, supplier="S01", language=settings.language)
        assert block(build_digest([closed], [], settings, DAY).text, heading)[1:] == lines, settings.language
    alone = case_result("250102_20261118T090000", "closed", writes=[prepared(attach), prepared(status)], supplier="S02")
    assert block(build_digest([alone], [], SETTINGS, DAY).text, "Behandelde zaken")[1] == "- PO 250102 (S02): afgesloten, 2 in Exact zelf te doen"
    kept = case_result("250103_20261118T090000", "closed", writes=[attach, status.model_copy(update={"status": "failed"})], supplier="S03")
    kept_lines = block(build_digest([kept], [], SETTINGS, DAY).text, "Behandelde zaken")
    assert kept_lines[1] == f"- {kept.digest_line}" and kept_lines[3] == "    - status: Besteld → Bevestigd (mislukt)"
    assert kept_lines[2] == f"    - bijlage MU01_conf.pdf [ongedaan maken]({undo_link(SETTINGS, kept.case_id, 'e3')})"
    cart = case_result("250201_20261118T090000", "closed", writes=writes[:2], supplier="S21").model_copy(
        update={"digest_line": "voorcontrole PO 250201 (S21): afgesloten, 2 voorbereide wijzigingen"})
    assert f"- {cart.digest_line}" in build_digest([cart], [], SETTINGS, DAY).text.splitlines()   # nothing to do by hand: the line as it is


# --- the three reasons that had no label (Task 44) ---

@pytest.mark.parametrize("settings, answered, skipped, unread", [
    (SETTINGS, "De leverancier heeft geantwoord: lees het antwoord", "Scan zonder leesbare tekst: bekijk dit document zelf",
     "Scan zonder leesbare tekst: de assistent kon dit document niet lezen; bekijk het zelf"),
    (ENGLISH, "The supplier answered: read the reply", "A scan without readable text: look at this document yourself",
     "A scan without readable text: the assistant could not read this document; look at it yourself")], ids=["nl", "en"])
def test_a_reply_a_skipped_scan_and_a_scan_that_failed_at_reading_have_their_label(settings, answered, skipped, unread):
    """Three reasons the digest showed as the records hold them, in English also in the Dutch digest, have a label in the page's
    language: the item of a supplier's reply; a scan the watched folder skipped, by the words of the tab "Bestanden"; and a case
    that failed at reading a scan, whole or one page of it, by the words that tab uses for it. Every other skip and every other
    error stays as it was."""
    from aid.pipeline import ANSWERED
    from aid.readers.copilot_client import NO_TEXT_LAYER, NO_TEXT_ON_PAGE
    from aid.review.i18n import STRINGS

    language = settings.language
    review = case_result("250112_20261118T091000", "review", supplier="S12", language=language).model_copy(update={"review_items": [item("RI1", ANSWERED)]})
    whole = case_result("250116_20261118T100000", "failed", supplier="S16", language=language).model_copy(update={"error": f"ReaderError: {NO_TEXT_LAYER}"})
    page = case_result("unknown_20261118T100500", "failed", language=language).model_copy(
        update={"error": f"ReaderError: {NO_TEXT_ON_PAGE.format(page=2)}\nmore detail"})
    other = case_result("250105_20261118T101000", "failed", language=language).model_copy(update={"error": "ReaderError: no template for scan.pdf"})
    d = build_digest([review, whole, page, other], [("mail.eml/scan.pdf", "no text layer; class unknown"), ("again.pdf", "duplicate")], settings, DAY)
    words = {"nl": ("Wacht op u", "Mislukt", "Overgeslagen", "onbekend"), "en": ("Waiting for you", "Failed", "Skipped", "unknown")}[language]
    assert block(d.text, words[0])[2:] == [f"    - {answered}"]
    assert block(d.text, words[1])[1:] == [f"- PO 250116 (S16): {unread}", f"- PO {words[3]}: {unread}", "- PO 250105: ReaderError: no template for scan.pdf"]
    assert block(d.text, words[2])[1:] == ["- again.pdf: duplicate", f"- mail.eml/scan.pdf: {skipped}"]
    assert (STRINGS[language]["reason:supplier answered: read the reply"], STRINGS[language]["files_skipped:scan"],
            STRINGS[language]["files_outcome:unread"]) == (answered, skipped, unread)
    for label in (answered, skipped, unread):
        assert f"{escape(label)}</li>" in d.html
    assert "text layer" not in d.text + d.html and "more detail" not in d.text + d.html and "class unknown" not in d.text + d.html
    assert "    - supplier answered: read the reply" not in d.text.splitlines()          # the reason as the records hold it
