"""The digest: one e-mail to the buyer about the cases the aid handled, with an undo link next to each write it made and a link to
the review page for each case that waits for the buyer (design specification, section 5). A Power Automate flow outside the code
relays it to Teams (aid/README.md, "Teams via Power Automate").

build_digest gives the digest of a day from the results of its cases and the files the watched folder skipped, in the language of
the settings, as plain text and as simple HTML with the same content. Its blocks come in this order, each left out when it is empty:
- Behandelde zaken (Handled cases): every case, in its digest line (CaseResult.digest_line), with a line under it for each of its
  writes: the field in words, the article, the line or the file it changed, the old and the new value, and the undo link of a write
  made. A change prepared says "(voorbereid)" and a write that failed "(mislukt)" instead: neither changed the ERP, so neither has
  anything to undo. Only a price, a delivery date or a quantity of a PO line and an article's purchase price are rows of the list
  "In Exact invoeren" (aid/execution/by_hand.py): the confirmation to attach (or detach) and the status that the aid prepared are on
  no list yet, so the buyer attaches the confirmation and sets the status in Exact himself, and their lines say "(zelf in Exact
  doen)" ("(do it in Exact yourself)") instead. They are not counted among the prepared changes of the case's line, which then counts
  them on their own, in the language of the settings: "PO 250101 (S01): afgesloten, 2 voorbereide wijzigingen, 2 in Exact zelf te
  doen" ("closed, 2 prepared changes, 2 to do in Exact yourself").
- Wacht op u (Waiting for you): the cases in review, each with its link to the review page and a line per review item: its reason
  by the review page's label (see below), then its field and its line when they are known. A settled item (the item of a message
  not sent that was sent since, ReviewItem.settled) has no line.
- Vraag gesteld aan de leverancier (Question sent to the supplier): the cases that wait for the supplier, each with the supplier and
  the template of each question sent in words, each template once, whatever the number of attempts. A request to the approver to
  approve a PO again is no question.
- Mislukt (Failed): the failed cases, each with the first line of its error. A case that failed at reading a scan, a document or a
  page of it without a text layer, which Copilot does not read (unread_scan), is named by the label of the tab "Bestanden" for it,
  in the language: "Scan zonder leesbare tekst: de assistent kon dit document niet lezen; bekijk het zelf".
- Overgeslagen (Skipped): the skipped files grouped by their reason, "<file>: <reason>", the reasons in alphabetical order. A scan
  the watched folder skipped is named by the words of the tab "Bestanden", in the language ("Scan zonder leesbare tekst: bekijk dit
  document zelf"); every other reason as the scan wrote it.
A case made from a try-out document (tryout_case of aid/tryout_mark.py) has " (proefdocument)" (" (try-out document)") behind its
line in every block, its own digest line with no count of what to do in Exact, and each of its prepared rows says "(proefdocument)"
in place of "(voorbereid)" and "(zelf in Exact doen)": the digest never tells the buyer to do anything in Exact for a try-out, and
the caller leaves its changes out of the count of the changes to enter (aid/runner.py).
While changes wait on the list "In Exact invoeren" of the review page (aid/execution/by_hand.py), the first line under the title,
before the blocks, gives their number and the link to that tab: "In Exact in te voeren: 2 wijzigingen. Open het tabblad In Exact
invoeren: <link>" ("To enter in Exact: 2 changes. Open the tab Enter in Exact: <link>"); the caller counts them (to_enter), and a
digest built without the count has no such line. When the caller could not read the list (to_enter None), that line says so, with
the same link, so that the digest does not look like a day with nothing to enter: "De lijst In Exact invoeren kon niet worden
gelezen. Open het tabblad om te zien wat er nog in te voeren is: <link>" ("The list Enter in Exact could not be read. Open the tab to
see what is still to enter: <link>"). While the runner cannot read the work mailbox because the Microsoft sign-in has
lapsed (aid/runner.py), one line under the title, before the blocks, says so: "Postvak niet verbonden: meld u opnieuw aan op het
tabblad Instellingen, onderaan bij Koppelingen" ("Mailbox not connected: sign in again on the Settings tab, at its bottom under
Connections"). While Microsoft 365 Copilot, the AI service, fails (it refused, could not be reached, or its sign-in has lapsed), a
line after it names the error and the number of cases waiting for Copilot: "Copilot werkt niet (<error>): 2 zaken wachten erop; meld
u aan voor Copilot of test Copilot op het tabblad Instellingen, onderaan bij Koppelingen". Both lines name the place of the buttons,
because the settings tab keeps them
folded away under "Koppelingen" at its bottom (aid/review/page.py). The documents that Copilot could not read while it read
others (it refused them, or failed them after reading another case) are a line of their own: "Copilot kon 1 document niet lezen;
nieuwe poging bij de volgende ronde".
A day without cases and without skipped files says so. A price or a quantity has the decimal comma in Dutch and the point in
English. A case is named by its PO number, the part of its id before the first underscore ("onbekend" for "unknown"), and by its
supplier when that is known. The reason of a review item is shown by its label on the review page, in the language
(i18n.STRINGS of aid/review/i18n.py, through reason_label: the label of the reason, or of the reason it begins with, as the page
labels "no answer in 3 working days"); a reason without a label, and an error, is shown by its first line as the routing or the
pipeline wrote it, without the name the pipeline gives a write (" (rule R04, po_line_price, line 1)"), because the digest shows no
rule code. Of the settings it shows only the address of the review page, in its links: never a threshold and never the
authorization limit. The text writes a link as [label](link), and a link whose label is its address as the address alone (the link
of the list); the HTML writes each link as an anchor and passes every value through html.escape.

The subject is "[Inkoopassistent] Dagoverzicht DD-MM-YYYY", in English "[Inkoopassistent] Daily digest DD-MM-YYYY": the prefix is
the same in both languages because the Power Automate flow matches it. undo_link and review_link are the links to the review page
(settings.review_url): <review_url>/?case=<case id> opens the page with that case selected, and &undo=<entry id> also selects that
write in the page's list of writes to undo, which the buyer then undoes with the undo button; opening the link undoes nothing. The
page offers the cases in review and the closed cases with a write made in the system that is not undone (aid/review/page.py).
list_link, <review_url>/?tab=invoeren, opens the page with the tab "In Exact invoeren" selected.
digest_due says when the digest is to be sent, and send_digest sends its text to the buyer through a mailer
(aid/messaging).

A result saved by an earlier version may hold what the vocabulary does not know: a kind, field or template without its words is
shown as it is, and a message record without a template is left out, so that one such result does not stop the whole digest.
"""
import re
from datetime import date, datetime, time
from html import escape
from pathlib import PureWindowsPath
from typing import NamedTuple

from pydantic import BaseModel

from aid.comparison import Flag, Uncompared
from aid.execution.ops import WriteOp
from aid.execution.writer import WriteLogEntry, read_before
from aid.messaging import QUESTIONS, Mailer, Message, SentRecord
from aid.pipeline import SENT, CaseResult, ReviewItem
from aid.pipeline import _WORDS as _CASE_WORDS, _digest_line
from aid.readers.copilot_client import NO_TEXT_LAYER
from aid.review.hand_list import TAB
from aid.review.i18n import STRINGS, reason_label
from aid.review.intake import SKIPPED
from aid.routing import findings_by_id
from aid.settings import Settings
from aid.tryout_mark import case_is_tryout

SUBJECT_PREFIX = "[Inkoopassistent]"             # the same in both languages: the Power Automate flow matches it
DIGEST = "digest"                                # the template of the digest's message, and the start of its case id
_AMOUNTS = ("po_line_price", "article_price", "po_line_quantity")      # the kinds whose values take the decimal sign of the language
_YOURS = ("attach_document", "detach_document", "set_status")  # prepared, but on no list yet: the buyer does them in Exact himself
_WRITE_NAME = re.compile(r" \((?:write: )?rule R\d+[^)]*\)")    # the pipeline's name of a write in a reason, " (rule R04, po_line_price,
                                                            # line 1)", and in the error of a case, " (write: rule R04, ...)"
# How the error of a case begins that failed at reading a scan: Copilot's refusal of a document, or of a page, without a text layer
# (NO_TEXT_LAYER and NO_TEXT_ON_PAGE of aid/readers/copilot_client.py, which begin with the same words).
_UNREAD_SCAN = f"ReaderError: {NO_TEXT_LAYER.partition(':')[0]}"
UNREAD = "files_outcome:unread"                  # the key of the label of such a case, on the tab "Bestanden" and in the digest
_WORDS = {
    "nl": {"digest": "Dagoverzicht", "handled": "Behandelde zaken", "review": "Wacht op u", "waiting": "Vraag gesteld aan de leverancier",
           "failed": "Mislukt", "skipped": "Overgeslagen", "nothing": "Er zijn geen zaken behandeld en geen bestanden overgeslagen.",
           "to_enter_one": "In Exact in te voeren: 1 wijziging. Open het tabblad In Exact invoeren: {link}",
           "to_enter_many": "In Exact in te voeren: {n} wijzigingen. Open het tabblad In Exact invoeren: {link}",
           "to_enter_unread": ("De lijst In Exact invoeren kon niet worden gelezen. Open het tabblad om te zien wat er nog in te voeren is: "
                               "{link}"),
           "mailbox": "Postvak niet verbonden: meld u opnieuw aan op het tabblad Instellingen, onderaan bij Koppelingen",
           "copilot": ("Copilot werkt niet ({error}): {waiting}; meld u aan voor Copilot of test Copilot op het tabblad Instellingen, "
                       "onderaan bij Koppelingen"),
           "copilot_one": "1 zaak wacht erop", "copilot_many": "{n} zaken wachten erop",
           "copilot_refused": "Copilot kon {documents} niet lezen; nieuwe poging bij de volgende ronde",
           "document_one": "1 document", "documents_many": "{n} documenten",
           "undo": "ongedaan maken", "open": "beoordelen", "status": {"prepared": "(voorbereid)", "failed": "(mislukt)"},
           "yours": "(zelf in Exact doen)", "yours_count": "{n} in Exact zelf te doen", "tryout": "(proefdocument)",
           "line": "regel", "lines": "regels", "of_document": "van het document", "unknown": "onbekend", "empty": "leeg", "decimal": ",",
           "kinds": {"po_line_price": "prijs", "po_line_date": "levertijd", "po_line_quantity": "aantal", "article_price": "artikelprijs",
                     "attach_document": "bijlage", "detach_document": "bijlage", "set_status": "status"},
           "fields": {"price": "prijs", "quantity": "aantal", "unit": "eenheid", "delivery_date": "levertijd",
                      "line_presence": "ontbrekende of extra regel", "required_info": "vereiste gegevens"},
           "questions": {"quantity_unit": "aantal of eenheid", "line_missing_extra": "ontbrekende of extra regel",
                         "missing_info": "ontbrekende gegevens"}},
    "en": {"digest": "Daily digest", "handled": "Handled cases", "review": "Waiting for you", "waiting": "Question sent to the supplier",
           "failed": "Failed", "skipped": "Skipped", "nothing": "No cases were handled and no files were skipped.",
           "to_enter_one": "To enter in Exact: 1 change. Open the tab Enter in Exact: {link}",
           "to_enter_many": "To enter in Exact: {n} changes. Open the tab Enter in Exact: {link}",
           "to_enter_unread": "The list Enter in Exact could not be read. Open the tab to see what is still to enter: {link}",
           "mailbox": "Mailbox not connected: sign in again on the Settings tab, at its bottom under Connections",
           "copilot": ("Copilot is not working ({error}): {waiting}; sign in for Copilot or test Copilot on the Settings tab, at its "
                       "bottom under Connections"),
           "copilot_one": "1 case waits for it", "copilot_many": "{n} cases wait for it",
           "copilot_refused": "Copilot could not read {documents}; {again} tried again at the next pass",
           "document_one": "1 document", "documents_many": "{n} documents",
           "undo": "undo", "open": "review", "status": {"prepared": "(prepared)", "failed": "(failed)"},
           "yours": "(do it in Exact yourself)", "yours_count": "{n} to do in Exact yourself", "tryout": "(try-out document)",
           "line": "line", "lines": "lines", "of_document": "of the document", "unknown": "unknown", "empty": "empty", "decimal": ".",
           "kinds": {"po_line_price": "price", "po_line_date": "delivery date", "po_line_quantity": "quantity", "article_price": "article price",
                     "attach_document": "attachment", "detach_document": "attachment", "set_status": "status"},
           "fields": {"price": "price", "quantity": "quantity", "unit": "unit", "delivery_date": "delivery date",
                      "line_presence": "missing or extra line", "required_info": "required information"},
           "questions": {"quantity_unit": "quantity or unit", "line_missing_extra": "missing or extra line",
                         "missing_info": "missing information"}}}


class Digest(BaseModel):
    subject: str
    text: str                                    # plain text, the body of the e-mail
    html: str                                    # the same content as simple HTML
    period: str                                  # the day of the digest in ISO form, "2026-11-18"


class _Link(NamedTuple):
    label: str
    url: str


_Line = list[str | _Link]                       # a line of the digest: its texts and its links


class _Item(NamedTuple):
    """A line of a block and the lines under it."""
    line: _Line
    under: list[_Line]


def undo_link(settings: Settings, case_id: str, entry_id: str) -> str:
    """The link to the review page that selects the case and, among its writes to undo, the write-log entry; the buyer undoes it
    with the page's undo button."""
    return f"{_review_url(settings)}/?case={case_id}&undo={entry_id}"


def review_link(settings: Settings, case_id: str) -> str:
    """The link to the review page that selects the case."""
    return f"{_review_url(settings)}/?case={case_id}"


def list_link(settings: Settings) -> str:
    """The link to the review page that selects the tab "In Exact invoeren", the list of the changes to enter in Exact."""
    return f"{_review_url(settings)}/?tab={TAB}"


def _review_url(settings: Settings) -> str:
    """settings.review_url without a trailing slash, as the links add their own."""
    return settings.review_url.rstrip("/")


def digest_due(now: datetime, last_sent: datetime | None, settings: Settings) -> bool:
    """Whether the digest is to be sent at now. Per case (settings.digest "per_case") it always is. Daily it is when now's time of
    day is at or past settings.digest_time ("HH:MM") and no digest was sent on now's date: last_sent is None or falls on an earlier
    date, taken on now's clock (in now's time zone; a time without a zone is local time)."""
    if settings.digest == "per_case":
        return True
    if now.time() < time.fromisoformat(settings.digest_time):
        return False
    return last_sent is None or last_sent.astimezone(now.tzinfo).date() < now.date()


def build_digest(results: list[CaseResult], skipped: list[tuple[str, str]], settings: Settings, period: date,
                 mailbox_lapsed: bool = False, copilot_down: tuple[str, int] | None = None, copilot_refused: int = 0,
                 to_enter: int | None = 0) -> Digest:
    """The digest of the day `period` from the results of its cases and the skipped files, (name, reason) pairs, in the language of
    the settings (see the module docstring), with the line of the changes to enter in Exact while to_enter, their number, is not 0,
    or the line that the list could not be read when to_enter is None, the line on the work mailbox when its sign-in has lapsed, the
    line on Copilot, (its error, the number of cases waiting for it), when it fails, and the line on the documents Copilot could not
    read while it read others, their number."""
    words = _WORDS[settings.language]
    notes: list[_Line] = []
    if to_enter is None or to_enter:
        key = "to_enter_unread" if to_enter is None else "to_enter_one" if to_enter == 1 else "to_enter_many"
        before, _, after = words[key].partition("{link}")
        link = list_link(settings)
        notes.append([before.format(n=to_enter), _Link(link, link), after])
    if mailbox_lapsed:
        notes.append([words["mailbox"]])
    if copilot_down is not None:
        error, waiting = copilot_down
        cases = words["copilot_one"] if waiting == 1 else words["copilot_many"].format(n=waiting)
        notes.append([words["copilot"].format(error=error, waiting=cases)])
    if copilot_refused:
        documents = words["document_one"] if copilot_refused == 1 else words["documents_many"].format(n=copilot_refused)
        again = "it is" if copilot_refused == 1 else "they are"
        notes.append([words["copilot_refused"].format(documents=documents, again=again)])
    title = f"{words['digest']} {period:%d-%m-%Y}"
    made_up = {result.case_id for result in results if case_is_tryout(settings.cases_root, result.case_id)}      # the cases of try-outs
    blocks = [(words["handled"], [_handled(result, settings, words, result.case_id in made_up) for result in results]),
              (words["review"], [_in_review(result, settings, words, result.case_id in made_up) for result in results if result.status == "review"]),
              (words["waiting"], [_waiting(result, words, result.case_id in made_up) for result in results if result.status == "waiting"]),
              (words["failed"], [_failed(result, words, result.case_id in made_up, settings.language) for result in results
                                 if result.status == "failed"]),
              (words["skipped"], [_Item([f"{name}: {_skip_reason(reason, settings.language)}"], [])
                                  for name, reason in sorted(skipped, key=lambda skip: skip[1])])]
    blocks = [(heading, items) for heading, items in blocks if items]
    return Digest(subject=f"{SUBJECT_PREFIX} {title}", text=_text(title, notes, blocks, words),
                  html=_html(title, notes, blocks, words, settings.language), period=period.isoformat())


def send_digest(d: Digest, mailer: Mailer, settings: Settings) -> SentRecord:
    """Send the text of the digest to the buyer (settings.buyer_email, no one in copy) under its subject through the mailer, as a
    message of the template "digest" and the case "digest-<period>", and return the mailer's record of it. The HTML is not sent:
    it stays in the Digest for the caller."""
    return mailer.send(Message(to=[settings.buyer_email], cc=[], subject=d.subject, body=d.text, template=DIGEST,
                               case_id=f"{DIGEST}-{d.period}"))


# --- the lines of the blocks ---

def _handled(result: CaseResult, settings: Settings, words: dict, tryout: bool = False) -> _Item:
    """A handled case: its line (_case_line), and a line per write under it; a case made from a try-out has its own digest line with
    the mark behind it, and its rows the mark (see the module docstring)."""
    line = f"{result.digest_line} {words['tryout']}" if tryout else _case_line(result, settings, words)
    return _Item([line], [_write(entry, result.case_id, settings, words, tryout) for entry in result.write_entries])


def _case_line(result: CaseResult, settings: Settings, words: dict) -> str:
    """The case's digest line (CaseResult.digest_line). When the aid prepared a confirmation to attach or a status (_yours), the line
    is made again as the pipeline makes it, in the language of the settings, without those among the prepared changes, and then
    counts them on their own: "PO 250101 (S01): afgesloten, 2 voorbereide wijzigingen, 2 in Exact zelf te doen"."""
    yours = sum(1 for entry in result.write_entries if _yours(entry))
    if not yours:
        return result.digest_line
    po_number = result.case_id.partition("_")[0]
    cart = result.digest_line.startswith(tuple(f"{case_words['cart']} " for case_words in _CASE_WORDS.values()))
    line = _digest_line(None if po_number == "unknown" else po_number, result.supplier, result.status,
                        [entry for entry in result.write_entries if not _yours(entry)], settings.language, "cart" if cart else "confirmation")
    return f"{line.removesuffix(', ' + _CASE_WORDS[settings.language]['none'])}, {words['yours_count'].format(n=yours)}"


def _yours(entry: WriteLogEntry) -> bool:
    """Whether the buyer does this write in Exact himself: a confirmation to attach or detach, or a status, that the aid prepared."""
    return entry.status == "prepared" and entry.op.kind in _YOURS


def _write(entry: WriteLogEntry, case_id: str, settings: Settings, words: dict, tryout: bool = False) -> _Line:
    """A write: the field in words and what it changed, then the old and the new value when it has either (an attached file has
    neither), then its undo link when it was made, else whether it was prepared or failed; a confirmation to attach or a status that
    was prepared says that the buyer does it in Exact himself, and any write a try-out's case prepared says "(proefdocument)"."""
    op = entry.op
    text = " ".join(part for part in (words["kinds"].get(op.kind, op.kind), _changed(op, words)) if part)
    if entry.previous is not None or entry.new is not None:
        # a write made in a system into a field that was empty then says so, as the tab and the put-back do
        before = words["empty"] if entry.status == "done" and read_before(entry) == "empty" else _value(entry.previous, op.kind, words)
        text += f": {before} → {_value(entry.new, op.kind, words)}"
    if entry.status == "done":
        return [f"{text} ", _Link(words["undo"], undo_link(settings, case_id, entry.entry_id))]
    if tryout and entry.status == "prepared":
        return [f"{text} {words['tryout']}"]
    if _yours(entry):
        return [f"{text} {words['yours']}"]
    return [f"{text} {words['status'].get(entry.status, entry.status)}"]


def _changed(op: WriteOp, words: dict) -> str:
    """What a write changed besides its field: the article, else the PO line ("regel 1"), else the file it attached or detached."""
    if op.article_code:
        return op.article_code
    if op.line_id:
        return f"{words['line']} {op.line_id}"
    return PureWindowsPath(op.document).name if op.document else ""     # PureWindowsPath cuts at either kind of separator


def _value(value: str | None, kind: str, words: dict) -> str:
    """A value of a write, "onbekend" when there is none (the old price of an article the aid has not read); a price or a quantity
    with the decimal sign of the language."""
    if value is None:
        return words["unknown"]
    return value.replace(".", words["decimal"]) if kind in _AMOUNTS else value


def _in_review(result: CaseResult, settings: Settings, words: dict, tryout: bool = False) -> _Item:
    """A case in review: the case and its review link, and a line per review item under it."""
    findings = findings_by_id(result.findings) if result.findings is not None else {}
    return _Item([f"{_case(result, words, tryout)}: ", _Link(words["open"], review_link(settings, result.case_id))],
                 [[_review_item(item, findings, words, settings.language)] for item in result.review_items if not item.settled])


def _review_item(item: ReviewItem, findings: dict[str, Flag | Uncompared], words: dict, language: str) -> str:
    """A review item: its reason by the review page's label in the language, else its first line, then its field and the lines of
    its finding when they are known: the PO's lines, or the document's when the PO has none (an extra line)."""
    known = [words["fields"].get(item.field, item.field)] if item.field is not None else []
    finding = findings.get(item.finding_id) if item.finding_id is not None else None
    if finding is not None and finding.po_line_ids:
        known.append(_lines(finding.po_line_ids, words))
    elif finding is not None and finding.doc_line_ids:
        known.append(f"{_lines(finding.doc_line_ids, words)} {words['of_document']}")
    label = reason_label(next(iter((item.reason or "").splitlines()), ""), STRINGS[language])    # as the page labels the reason
    return (label or _shown(item.reason)) + (f" ({', '.join(known)})" if known else "")


def _waiting(result: CaseResult, words: dict, tryout: bool = False) -> _Item:
    """A case that waits for the supplier: the case, with the template of each of its questions that was sent, in words, each
    template once whatever the number of attempts (a close that could not send records a message not sent)."""
    templates = dict.fromkeys(message.get("template") for message in result.messages if message.get("status") == SENT)
    asked = [words["questions"].get(template, template) for template in templates if template in QUESTIONS]
    return _Item([_case(result, words, tryout) + (f": {', '.join(asked)}" if asked else "")], [])


def unread_scan(result: CaseResult) -> bool:
    """Whether the case failed at reading a scan: its document, or a page of it, has no text layer, which Copilot does not read
    (aid/readers/copilot_client.py). The parser's failure on a scan is not told apart: it names no template, as for any document no
    template fits."""
    return result.status == "failed" and (result.error or "").startswith(_UNREAD_SCAN)


def _failed(result: CaseResult, words: dict, tryout: bool = False, language: str = "nl") -> _Item:
    """A failed case: the case, with the first line of its error; a case that failed at reading a scan (unread_scan) with the label
    of the tab "Bestanden" for it, in the language."""
    error = STRINGS[language][UNREAD] if unread_scan(result) else _shown(result.error)
    return _Item([_case(result, words, tryout) + (f": {error}" if error else "")], [])


def _skip_reason(reason: str, language: str) -> str:
    """The reason of a skipped file as the block shows it: a scan without a text layer by the words of the tab "Bestanden" for it
    (SKIPPED of aid/review/intake.py), in the language; any other reason as it is."""
    return STRINGS[language]["files_skipped:scan"] if SKIPPED.get(reason) == "scan" else reason


def _case(result: CaseResult, words: dict, tryout: bool = False) -> str:
    """A case as the blocks name it: "PO 250101", or "PO 250101 (S01)" when its supplier is known, with " (proefdocument)" behind it
    when it is made from a try-out."""
    po_number = result.case_id.partition("_")[0]
    return (f"PO {words['unknown'] if po_number == 'unknown' else po_number}" + (f" ({result.supplier})" if result.supplier else "")
            + (f" {words['tryout']}" if tryout else ""))


def _lines(line_ids: list[str], words: dict) -> str:
    """Lines by their ids: "regel 1", or "regels 2, 3"."""
    return f"{words['lines'] if len(line_ids) > 1 else words['line']} {', '.join(line_ids)}"


def _shown(text: str | None) -> str:
    """The first line of a reason or an error, without the name of a write that the pipeline puts in it, which names its rule."""
    return _WRITE_NAME.sub("", next(iter((text or "").splitlines()), ""))


# --- the text and the HTML ---

def _text(title: str, notes: list[_Line], blocks: list[tuple[str, list[_Item]]], words: dict) -> str:
    """The digest as plain text: the title, the notes after a blank line, then each block after a blank line, its heading and a line
    "- ..." per item, with the lines under the item indented; a link is written [label](link), or as its address alone when that is
    its label."""
    lines = [title] + (["", *(_plain(note) for note in notes)] if notes else [])
    for heading, items in blocks:
        lines += ["", heading]
        for item in items:
            lines.append(f"- {_plain(item.line)}")
            lines += [f"    - {_plain(line)}" for line in item.under]
    if not blocks:
        lines += ["", words["nothing"]]
    return "\n".join(lines) + "\n"


def _html(title: str, notes: list[_Line], blocks: list[tuple[str, list[_Item]]], words: dict, language: str) -> str:
    """The digest as simple HTML: the title as the page's heading, each note as a paragraph, each block as a heading and a list, the
    lines under an item as a list in it, and each link as an anchor; every value passes through html.escape."""
    parts = ["<!DOCTYPE html>", f'<html lang="{language}">', f'<head><meta charset="utf-8"><title>{escape(title)}</title></head>',
             "<body>", f"<h1>{escape(title)}</h1>", *(f"<p>{_marked(note)}</p>" for note in notes)]
    for heading, items in blocks:
        parts += [f"<h2>{escape(heading)}</h2>", "<ul>"]
        for item in items:
            under = "".join(f"<li>{_marked(line)}</li>" for line in item.under)
            parts.append(f"<li>{_marked(item.line)}" + (f"<ul>{under}</ul>" if under else "") + "</li>")
        parts.append("</ul>")
    if not blocks:
        parts.append(f"<p>{escape(words['nothing'])}</p>")
    return "\n".join([*parts, "</body>", "</html>"]) + "\n"


def _plain(line: _Line) -> str:
    return "".join((part.url if part.label == part.url else f"[{part.label}]({part.url})") if isinstance(part, _Link) else part
                   for part in line)


def _marked(line: _Line) -> str:
    return "".join(f'<a href="{escape(part.url)}">{escape(part.label)}</a>' if isinstance(part, _Link) else escape(part) for part in line)
