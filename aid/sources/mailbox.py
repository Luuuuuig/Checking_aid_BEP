"""The watched folder: the files that the buyer's mailbox rule saves for the aid, found, classified and filed.

scan_folder looks at the files that lie directly in the folder, in the order of their names. It does not look at the folders in
it, so it never touches processed/. It runs no pipeline: it reads the first page of a document and, for a confirmation without a
PO number, the quick parser read that the PO store needs, and it opens the case or files the PO.

Unpacking. An .eml file is opened with the standard library's email package (extract_eml). Its text is the subject and the body,
and each attachment, at whatever depth the e-mail packs it, is handled as a file of its own, with the text of the e-mail as context.
An attached e-mail is one attachment and is not opened. A .pdf or .png file is handled as it is. A .csv or .xlsx file is an export
of the ERP when the setting export_columns is not empty and one of its rows holds every column title of that map (is_export of
aid/readers/po_export.py; see "Exports" below). While export_columns is set, any other .csv or .xlsx file is skipped as
"spreadsheet without the export's column titles" (NOT_AN_EXPORT): no row of it holds every title of the map, a price list for one,
or it cannot be read as a table at all, a damaged workbook or a CSV file in an encoding the export reader does not read; so the
digest says why the file was not read. With export_columns empty every spreadsheet is a file of another type. A file or an
attachment of another type is skipped as an unsupported file type, and an e-mail without attachments as "no attachment". In the
list of skipped items an attachment is named "<e-mail>/<attachment>", and a file that is not in an e-mail by its own name; the list
is by file, and an e-mail's attachments are in their order in the e-mail. The attachments are written into a folder of the scan's
own under <cases_root>/tmp/work (work_copy_folder of aid/case.py), made when the scan has an e-mail and removed when the scan is
done, and never into the system's temporary folder, outside the aid's folders.

Answers. An .eml that answers a question of the aid, by the Message-ID that its In-Reply-To or References header names (the
message id of a record in a case's messages/sent.jsonl other than a re-approval request), is filed into that case with file_answer of aid/pipeline.py, which
sorts a reply, a bounce and an automatic reply with file_reply of aid/messaging; a real reply puts the case in review, so that its
wait ends and the buyer reads it. Such an e-mail is handled: it moves to processed/, its attachments open no case, and it is listed
in ScanResult.replies as (file, case id), and its case in ScanResult.answered when it is a real reply. An e-mail that answers no
question of the aid is unpacked as any other. A case whose saved result cannot be read leaves the e-mail in the folder as an error,
with no copy filed.

Duplicates. A document whose sha256 is already an input file of a case is skipped as "duplicate", whatever it would have been. The
cases are searched for it once for each file, before the classification, so that a file that is sent again costs no decision and is a
duplicate also when the decider cannot be reached. A copy of a confirmation in the same scan, which has no case yet when it is
classed, is found when its turn comes, by the hashes of the confirmations that this scan has opened a case for. Two kinds of
document are not searched for: an export of the ERP, which is told apart before the search, and a PDF that the layout of the
settings makes a PO PDF (see "A PO layout in the settings" below).

Exports. An export of the ERP, the order lines saved from an overview, gives a cart pre-check its PO side (aid/readers/po_export.py).
It is filed with PoStore.from_export and the three export settings: each of its POs whose rows differ from those of the PO's latest
look gets the next look, beside the PO's PDF versions (aid/po_store.py), and a PO whose rows are the same files nothing. An export
file is filed once: the same file given again, also after a newer export of its POs, and a file or an e-mail that stays in the
folder and is handled again at every pass, files nothing more (the store knows the file by its sha256), so an older export never
comes back on top of a newer one. The exports are filed before the PO PDFs and the confirmations of the same scan, and among
themselves in the order of the modification times of their files in the folder, an attachment by the time of its e-mail file, the
name breaking a tie only: of two exports of one PO that wait for the same pass the newer stands, whatever their names. Each is
listed in ScanResult.stored_exports with the PO numbers of the file and those whose look it filed (none when it is given again). An
export that the store refuses is an error, as a refused PO PDF is: it stays in the folder, or its e-mail does, and the reason names
the file with the column or the row, never what a cell holds. Such a refusal comes after is_export took the file: a title of the
map that stands twice in the title row, or a row the reader refuses (a row with an item code and no PO number without fill-down, a
PO number that is a date, holds no digit or cannot be a folder name, a line id that is blank or stands twice).

Classes. classify_attachment gives a document one of the classes of DocClass by the keywords of KEYWORDS, looked for as whole words
on the first page, in lower case and without accents ("Factuuradres" is not "factuur"), in three steps.
1. A keyword of a confirmation anywhere on the page makes the document a confirmation. A confirmation names the PO (the made-up ones
   have "Uw inkooporder 250101" on row 3) and may begin a line with the words of a PO ("Purchase order confirmation"), so it is told
   first.
2. Otherwise the first of the first TITLE_LINES (three) lines of the page that begins with a keyword, of any class, decides. A PO
   gives its title there ("Inkooporder 250101", "Purchase order: 4500123") and may say "Invoice address" or "Delivery note must
   accompany the goods" further down. A line that only mentions the PO ("Uw inkooporder 250101") does not begin with the word, and a
   document that opens with "Factuur" is an invoice although its next line says "Purchase order: 250102". The keywords of po_pdf
   (TITLE_CLASSES) count nowhere else, "bestellung" being an everyday word. A line that does begin with the word, such as the label
   "Inkooporder: 250101" of a reference field, still counts as a title when no earlier title line begins with a keyword.
3. Otherwise the keywords of the other classes count anywhere on the page, class by class in the order of KEYWORDS: packing_slip,
   invoice, quotation.
When no keyword is found the decider is asked, if there is one: a "document_type" decision with the classes as options and the file
name, the first page and the text of the e-mail as context. Its choice counts when its confidence is at least MIN_CONFIDENCE.
Otherwise the class is "other". A document without a text layer (a scan, a PNG) has no first page to read, so only the decider can
class it. When it cannot, the document is skipped as "no text layer; class unknown" and a review item is written. When it classes the
document as "other" with confidence, the document is skipped as "other" and no review item is written: the aid then knows that it is
not a document it handles, a signature image for one.

A PO layout in the settings. With settings.po_layout set, the layout of the PO PDF that the ERP e-mails, the first page of every PDF
is first held against that layout, as the layout reader reads it (layout_first_page and fits of aid/readers/po_pdf.py: the whole
title row, word for word, and the PO label with a value after it), before the cases are searched for a duplicate and before any
keyword counts. A PDF that fits is a PO PDF whatever else its first page says below its column titles, so a PO that asks its
supplier for an "orderbevestiging" under its lines is filed and not taken for a confirmation, and it is filed also when a case
already holds its bytes, as when it opened a case before the layout was set. Two exceptions keep a supplier's document out of the PO
store:
- a PDF whose header, the lines of its first page above its first title row (header_lines of aid/readers/po_pdf.py), holds a
  keyword of a confirmation as a whole word or phrase anywhere (titled_confirmation) is classed by the keywords, as without a layout,
  although it fits: a supplier's confirmation may repeat the PO's own label and title row, with its title under a letterhead. A PO
  of the company whose header holds such a keyword is taken for a confirmation in the same way;
- when settings.po_senders is not empty, a PDF that fits and came in an e-mail whose From address (sender_of) is not one of those
  senders, an address of the list or an address on a domain of the list, is a confirmation, also when the keywords alone would
  call it a PO: a supplier may answer with the PO itself. Such a PDF is searched for as a duplicate as any confirmation is. An
  e-mail without a From address is from nobody on the list. A file that lies in the folder by itself has no sender and passes.
A PDF that does not fit is classed as without a layout. The store reads every PO PDF with the layout and with nothing else, so a
PDF that the keywords class as a PO and that does not fit the layout is refused by the store and stays in the folder as an error.

What happens to a document, by class:
- an export: filed in the PO store as looks (stored_exports; see "Exports" above).
- po_pdf: filed in the PO store (stored_pos), read with the layout of the settings when they hold one. A PDF that the store holds
  already is filed as the version it is, and listed all the same. A PDF that the store refuses is an error.
- confirmation: the PO numbers in the text of the e-mail and on the first page (find_po_numbers with the PO number pattern of the
  settings) decide:
    two or more different ones: skipped as "more than one PO number", and a review item is written;
    exactly one: a case is opened for that PO;
    none: the confirmation is read quickly with the layout template that fits it, and the PO store is asked which POs have at least
          two of its item codes (PoStore.find_by_lines). If that is exactly one PO, the case is opened for it. Otherwise (no
          template fits, the template does not fit the PDF, no PO or several) the case is opened with no PO number, which the
          routing sends to review because no version can be chosen. The pipeline's own reader may still read the file in full.
  The case is opened with the document as its only input file. Cases of one PO opened in the same second would get one case id, so a
  later case is opened one second after the earlier one, and so on, until its id is free.
- any other class: skipped with the name of the class.
The exports are filed first, then the PO PDFs, and only then are the confirmations matched, so that a PO and its confirmation that
arrive together find each other.

Filing. A file that was handled, whatever came of it, moves to processed/ in the folder under its own name, or with _2, _3 and so on
before the extension when processed/ holds that name already. A file that raised an exception (a PDF that cannot be read, an export
that the store refuses, a decider that cannot be reached) stays where it is and is skipped as "error: <message>"; the next scan
tries it again, and so classes it again, which asks the decider again when no keyword settles the class. An e-mail stays when any of
its attachments raised, and the attachments that were handled are then handled again: a case for a confirmation is not opened twice
(the duplicate check), the PO store files a PDF once, and an export given again files nothing.

Review items. cases_root/review_items.jsonl has one JSON line for each item that the buyer must look at, appended:
{"time": <the scan's time>, "file": <the name used in the list of skipped items>, "reason": <the reason>}.
"""
import hashlib
import json
import mimetypes
import os
import re
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel

from aid.case import TEMPORARY, WORK, Case, find_case_by_hash, open_case, sha256_file, work_copy_folder
from aid.decider import MIN_CONFIDENCE, Decider, Decision
from aid.pipeline import file_answer, is_real_reply
from aid.po_store import PoStore
from aid.readers.pdf_text import extract_lines
from aid.readers.po_export import EXPORT_SUFFIXES, is_export
from aid.readers.po_layout import PoLayout
from aid.readers.po_pdf import fits, header_lines, layout_first_page
from aid.readers.supplier_parser import LayoutTemplate, detect_template, load_templates, read_with_template
from aid.settings import Settings

DocClass = Literal["po_pdf", "confirmation", "packing_slip", "invoice", "quotation", "other"]
EXPORT = "export"                                # the kind of an export of the ERP: told apart before the classes, and none of them
# Why a .csv or .xlsx file is skipped while export_columns is set: is_export does not take it, because no row of it holds every column
# title of the map or it cannot be read as a table (see the module docstring).
NOT_AN_EXPORT = "spreadsheet without the export's column titles"

# The keywords of each class; the order of the classes matters (see the module docstring). Compared as whole words after lower-casing
# and removing accents, so "auftragsbestätigung" also finds "AUFTRAGSBESTÄTIGUNG" and "Auftragsbestatigung", and "factuur" does not
# find "Factuuradres".
KEYWORDS: dict[DocClass, list[str]] = {
    "confirmation": ["orderbevestiging", "opdrachtbevestiging", "auftragsbestätigung", "bestellbestätigung", "order confirmation",
                     "order acknowledgement", "order acknowledgment", "acknowledgement of order"],
    "po_pdf": ["inkooporder", "purchase order", "bestellung"],
    "packing_slip": ["pakbon", "lieferschein", "packing slip", "delivery note"],
    "invoice": ["factuur", "rechnung", "invoice"],
    "quotation": ["offerte", "angebot", "quotation", "quote"],
}
TITLE_LINES = 3                                  # the lines at the top of the first page that give a document its title
TITLE_CLASSES = ("po_pdf",)                      # the classes whose keywords count only where one of those lines begins with them

REVIEW_ITEMS_FILE = "review_items.jsonl"
PROCESSED_FOLDER = "processed"
DOCUMENT_SUFFIXES = (".pdf", ".png")             # the types of file that are classified; an .eml is opened for its attachments
CONTEXT_CHARS = 4000                             # the most of the e-mail text, and of the first page, that a decision is given
CASE_ID_TRIES = 1000                             # how many seconds after the time of the scan a free case id is looked for
NAME_BYTES = 200                                 # the longest name, in UTF-8 bytes, that an attachment is written under
RESOURCE_TYPES = ("image", "text", "audio", "video", "font")      # what a multipart/related holds besides its body: its pictures and styles

_OPTIONS_MEAN = {
    "po_pdf": "a purchase order of the buyer's company, as it is sent to a supplier",
    "confirmation": "a supplier's confirmation of a purchase order",
    "packing_slip": "a packing slip or delivery note that comes with goods",
    "invoice": "an invoice",
    "quotation": "a quotation or offer",
    "other": "anything else, such as a logo, a signature image, general terms or a data sheet",
}


class ScanResult(BaseModel):
    new_cases: list[str] = []                    # the ids of the cases opened, in the order of the files
    stored_pos: list[str] = []                   # the PO number of each PO PDF filed in the store
    skipped: list[tuple[str, str]] = []          # (name, reason) of each item left alone: by file, and an e-mail's attachments in their order
    replies: list[tuple[str, str]] = []          # (file, case id) of each e-mail filed as an answer to a question of the aid
    answered: list[str] = []                     # the case id of each of those that is a real reply (no bounce, no automatic reply)
    # (the name as the skipped list names it, the PO numbers of the file, and those of them that were new or changed, whose look it
    # filed) of each export of the ERP filed in the store, in the order of filing
    stored_exports: list[tuple[str, list[str], list[str]]] = []


# --- classes ---

def classify_attachment(path: Path, text: str | None, decider: Decider | None = None, email_text: str | None = None) -> DocClass:
    """The class of the document (see the module docstring): the class that the keywords give, whole words on the first page, a
    confirmation first, then the first of the title lines that begins with a keyword, then the other classes in the order of KEYWORDS;
    else the class the decider chooses with a confidence of at least MIN_CONFIDENCE, else "other".

    `text` is the text of the first page; None reads it from the file, which is "" for a file that is not a PDF and for a scan.
    `email_text` is the text of the e-mail that carried the file, if any; only the decider's question holds it. The decider is not
    asked when a keyword settles the class, and it is asked at most once."""
    return _classify(Path(path), text, decider, email_text) or "other"


def _classify(path: Path, text: str | None, decider: Decider | None, email_text: str | None) -> DocClass | None:
    """classify_attachment, but None where it would say "other" for want of an answer, so that a scan that the decider
    could not class differs from one that it classed as "other"."""
    if text is None:
        text = _first_page_text(path)
    doc_class = _by_keywords(text)
    if doc_class is None and decider is not None:
        doc_class = _ask(decider, path, text, email_text)
    return doc_class


def _by_keywords(text: str) -> DocClass | None:
    """The class that the keywords of KEYWORDS give the text, by the rules of the module docstring, or None."""
    page = _fold(text)
    titles = [_fold(line) for line in [line for line in text.splitlines() if line.strip()][:TITLE_LINES]]
    if any(_has_word(page, _fold(word)) for word in KEYWORDS["confirmation"]):
        return "confirmation"
    for title in titles:                                             # the first title line that begins with a keyword decides
        for doc_class, words in KEYWORDS.items():
            if any(_begins_with(title, _fold(word)) for word in words):
                return doc_class
    for doc_class, words in KEYWORDS.items():                        # then the classes whose keywords count anywhere on the page
        if doc_class not in TITLE_CLASSES and any(_has_word(page, _fold(word)) for word in words):
            return doc_class
    return None


def titled_confirmation(text: str, layout: PoLayout) -> bool:
    """Whether a first page that fits the PO layout says in its header that it is a confirmation: a line above its first title row
    (header_lines of aid/readers/po_pdf.py) holds a keyword of a confirmation as a whole word or phrase, anywhere on the line, in any
    case and with or without its accents. A keyword below the title row is no title."""
    return any(_has_word(_fold(line), _fold(word)) for line in header_lines(text, layout) for word in KEYWORDS["confirmation"])


def _ask(decider: Decider, path: Path, text: str, email_text: str | None) -> DocClass | None:
    """The class that the decider chooses with enough confidence, or None."""
    options = list(get_args(DocClass))
    context: dict = {"file_name": path.name, "what_the_options_mean": _OPTIONS_MEAN}
    if email_text and email_text.strip():
        context["email_text"] = email_text[:CONTEXT_CHARS]
    if text.strip():
        context["first_page"] = text[:CONTEXT_CHARS]
    answer = decider.decide(Decision(kind="document_type", options=options, context=context))
    return answer.choice if answer.choice in options and answer.confidence >= MIN_CONFIDENCE else None


def _first_page_text(path: Path) -> str:
    """The text of the first page of a PDF, a line to each text line; "" for a scan and for a file that is not a PDF. A .pdf
    that cannot be read raises."""
    if path.suffix.lower() != ".pdf":
        return ""
    return "\n".join(line.text for line in extract_lines(path) if line.page == 1)


def _fold(text: str) -> str:
    """The text as keywords are looked for in it: without accents, in lower case, with single spaces."""
    bare = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(bare.casefold().split())


def _has_word(folded: str, word: str) -> bool:
    """Whether the folded text holds the folded word or words as whole words, that is, with no letter, digit or underscore touching them.
    Lookarounds and not \\b, so that a word that starts or ends with punctuation ("c.o.d.") is found too."""
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", folded) is not None


def _begins_with(folded: str, word: str) -> bool:
    """Whether the folded line begins with the folded word or words, as a whole word."""
    return re.match(rf"{re.escape(word)}(?!\w)", folded) is not None


# --- PO numbers ---

def find_po_numbers(text: str, pattern: str) -> list[str]:
    """The PO numbers in the text, each once, in the order in which they first appear. `pattern` is a regular expression whose
    first group is the number (the PO number pattern of the settings); it is matched without regard to case. A pattern without a
    group gives the whole match."""
    numbers: list[str] = []
    for found in re.finditer(pattern, text, re.IGNORECASE):
        number = found.group(1) if found.re.groups else found.group(0)
        if number and number not in numbers:
            numbers.append(number)
    return numbers


# --- e-mails ---

def extract_eml(path: Path, out_dir: Path) -> tuple[str, list[Path]]:
    """Open the e-mail file with the standard library and return (text, attachments).

    The text is the subject and then the body, a line to each line of text. The body is the plain-text part; an e-mail that has only
    an HTML part (or a plain-text part with nothing in it) gives the HTML part with its tags stripped, its entities turned into
    characters and its scripts and styles left out.

    The attachments are the leaf parts of the e-mail that are not its body, at any depth. Every multipart is searched, those that make
    the body too (an alternative, a related), so a PDF in the HTML branch of an alternative, beside the pictures of a related or in a
    multipart inside a signed message is found (a signature part is an attachment as well). A multipart/related holds the body and its
    resources: its pictures, styles and other media (image, text, audio, video, font) are no attachments unless they say so, and its
    other parts, a PDF for one, are. A text part with no file name that does not say it is an attachment is body text, however many
    there are. A forwarded message is one attachment and is not opened.

    The attachments are written to out_dir/<the first 12 hex digits of the sha256 of the e-mail file>/ under their own names, made
    safe to write: no folders, none of the characters that file systems refuse, no dots or spaces at the ends, at most NAME_BYTES
    bytes. A name without an extension takes the extension of the type of its part (application/pdf, image/png, message/rfc822 and the
    types the system knows), and an attachment without a name is called attachment_<n>. A name that an earlier attachment of the same
    e-mail has taken gets _2, _3 and so on before its extension. They are returned in the order of the e-mail. Reading the same e-mail
    again writes the same files again."""
    data = Path(path).read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(data)
    subject = " ".join(str(message["subject"] or "").split())
    text = "\n".join(part for part in (subject, _body_text(message)) if part)
    folder = Path(out_dir) / hashlib.sha256(data).hexdigest()[:12]
    attachments: list[Path] = []
    taken: set[str] = set()
    for number, part in enumerate(_attachments(message), 1):
        name = _free_name(_attachment_name(part, number), lambda candidate: candidate.casefold() in taken)
        taken.add(name.casefold())
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(_payload(part))
        attachments.append(folder / name)
    return text, attachments


def sender_of(eml: Path) -> str | None:
    """The address of the From header of the e-mail file, in lower case: of several addresses the first. None for an e-mail without
    a From header, and for one whose From header holds no address with a name before the at sign and a domain after it."""
    message = BytesParser(policy=policy.default).parsebytes(Path(eml).read_bytes(), headersonly=True)
    sender = message["From"]
    addresses = sender.addresses if sender is not None else ()
    return next((f"{address.username}@{address.domain}".lower() for address in addresses if address.username and address.domain), None)


def _sent_by(sender: str, senders: list[str]) -> bool:
    """Whether the address is one of the senders: an address of the list, or an address on a domain of the list, which is an entry
    without a name before the at sign, written with or without the at sign. Case and outer spaces do not count. A domain under a
    listed domain is another domain."""
    domain = sender.rpartition("@")[2]
    return any(entry == sender or entry.removeprefix("@") == domain for entry in (entry.strip().lower() for entry in senders))


def _body_text(message: EmailMessage) -> str:
    """The text of the body (see extract_eml): the plain-text part, else the HTML part, and "" when neither has text."""
    for kind in ("plain", "html"):
        part = message.get_body(preferencelist=(kind,))
        if part is not None:
            text = _text_of(part)
            text = _clean(_strip_tags(text) if kind == "html" else text)
            if text:
                return text
    return ""


def _text_of(part: EmailMessage) -> str:
    """The text of a text part. A character set that Python does not know is read as UTF-8, so that one e-mail cannot stop a scan."""
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        return part.get_payload(decode=True).decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    """The text with single spaces, without indentation and without empty lines."""
    return "\n".join(line for line in (" ".join(line.split()) for line in text.splitlines()) if line)


class _TextFromHtml(HTMLParser):
    """The text of an HTML page: no tags, no scripts or styles, a line break where a block ends and a space between table cells."""
    BREAKS = {"br", "p", "div", "tr", "li", "ul", "ol", "table", "blockquote", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
    CELLS = {"td", "th"}
    HIDDEN = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0                          # how deep inside a script or style the parser is

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")
        elif tag in self.CELLS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(self.hidden - 1, 0)
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _strip_tags(markup: str) -> str:
    parser = _TextFromHtml()
    parser.feed(markup)
    parser.close()
    return "".join(parser.parts)


def _attachments(message: EmailMessage) -> Iterator[EmailMessage]:
    """The parts of the e-mail that are attachments, however deep they lie (see extract_eml)."""
    if message.get_content_maintype() != "multipart":
        # One part is the whole message. It is the text to read, or a file that was sent without anything around it (a scanned PDF).
        if message.is_attachment() or message.get_content_type() not in ("text/plain", "text/html"):
            yield message
        return
    yield from _attachments_in(message)


def _attachments_in(container: EmailMessage) -> Iterator[EmailMessage]:
    """The attachments in a multipart, however deep. Every part that is itself a multipart is searched in its turn, those that make the
    body too (an alternative, a related): a PDF can lie in the HTML branch of an alternative, or beside the pictures of a related. The
    other parts are decided here. A related holds the body and its resources, so a part of it counts when it is not media (image, text,
    audio, video, font) or says it is an attachment: its pictures are left out and its PDF is kept. In any other multipart the library
    decides with iter_attachments(): it leaves out the first text/plain and the first text/html, and everything in an alternative, whose
    parts are versions of the body. A text part for reading is no attachment in either."""
    related = container.get_content_subtype() == "related"
    listed = set() if related else {id(part) for part in container.iter_attachments()}
    for part in container.iter_parts():
        if part.get_content_maintype() == "multipart":
            yield from _attachments_in(part)
        elif related and not _is_resource(part):
            yield part
        elif not related and id(part) in listed and not _is_body_text(part):
            yield part


def _is_resource(part: EmailMessage) -> bool:
    """Whether a part is a picture, style sheet or other medium of a body, one that does not say it is an attachment."""
    return part.get_content_maintype() in RESOURCE_TYPES and not part.is_attachment()


def _is_body_text(part: EmailMessage) -> bool:
    """Whether a part is text for reading: text/plain or text/html with no file name that does not say it is an attachment. The
    library leaves out the first of each only; Apple Mail writes the text after an inline PDF as a second text/plain."""
    return part.get_content_type() in ("text/plain", "text/html") and part.get_filename() is None and not part.is_attachment()


_EXTENSIONS = {"application/pdf": ".pdf", "image/png": ".png", "message/rfc822": ".eml"}       # fixed here: the system's table may differ


def _attachment_name(part: EmailMessage, number: int) -> str:
    """The name an attachment is written under, before a name that is taken gets a number (see extract_eml)."""
    last = re.split(r"[\\/]", part.get_filename() or "")[-1]                       # a path in the name leaves only its last part
    name = re.sub(r"[\x00-\x1f\x7f:*?\"<>|]", "_", last).strip(" .") or f"attachment_{number}"
    if not os.path.splitext(name)[1]:
        content_type = part.get_content_type()
        name += _EXTENSIONS.get(content_type) or mimetypes.guess_extension(content_type) or ""
    stem, suffix = os.path.splitext(name)
    suffix = suffix[:16]
    return stem.encode()[:NAME_BYTES - len(suffix.encode())].decode(errors="ignore") + suffix       # cut on a character boundary


def _free_name(name: str, is_taken: Callable[[str], bool]) -> str:
    """The name, or the name with _2, _3 and so on before its extension when `is_taken` says that the name is taken."""
    stem, suffix = os.path.splitext(name)
    chosen, number = name, 2
    while is_taken(chosen):
        chosen, number = f"{stem}_{number}{suffix}", number + 1
    return chosen


def _payload(part: EmailMessage) -> bytes:
    """The bytes of an attachment as the sender attached them. A forwarded message holds a message and not bytes: its own text."""
    data = part.get_payload(decode=True)
    if data is None:
        data = b"".join(inner.as_bytes() for inner in part.iter_parts())
    return data


# --- the folder ---

@contextmanager
def _unpacking(cases_root: Path, files: list[Path]) -> Iterator[Path | None]:
    """The folder for the attachments of the e-mails among the files: one of the scan's own under <cases_root>/tmp/work (aid/case.py),
    made only when there is an e-mail and removed when the scan is done, never in the system's temporary folder; None without an
    e-mail, which unpack then never needs."""
    if not any(path.suffix.lower() == ".eml" for path in files):
        yield None
        return
    with work_copy_folder(cases_root / TEMPORARY / WORK, "aid_mailbox_") as folder:
        yield folder


def scan_folder(folder: Path, store: PoStore, cases_root: Path, settings: Settings, now: datetime,
                decider: Decider | None = None) -> ScanResult:
    """Handle every file in the folder as the module docstring says, and return what came of it.

    Exports and PO PDFs go into `store`, cases are opened under `cases_root`, `settings` gives the PO number pattern and the export
    settings, and `now` is the time of the scan: the time in the id of each case and in each review item. A file whose name starts
    with a dot (".DS_Store") is neither read, skipped nor moved, and so is a folder. Raises FileNotFoundError if the folder does not
    exist."""
    folder = Path(folder)
    files = sorted(path for path in folder.iterdir() if path.is_file() and not path.name.startswith("."))
    modified = {path: _modified(path) for path in files}
    scan = _Scan(folder, store, Path(cases_root), settings, now, decider)
    with _unpacking(Path(cases_root), files) as scratch:          # the attachments of the e-mails, until the scan is done
        documents = [document for number, path in enumerate(files) for document in scan.unpack(number, path, scratch)]
        # The exports first, by the time of their file (of its e-mail for an attachment), then the PO PDFs, then the rest; within
        # each the order of the files, which also breaks a tie of times.
        for document in sorted(documents, key=lambda d: (d.kind != EXPORT, d.kind != "po_pdf", modified[d.origin] if d.kind == EXPORT else 0,
                                                         d.place)):
            scan.handle(document)
    scan.file_away(files)
    return scan.finish()


def _modified(path: Path) -> int:
    """The modification time of a file of the folder in nanoseconds, by which the exports of a pass are filed; 0 for a file that
    cannot be looked at, which its own handling then reports."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


Place = tuple[int, int]                          # (the number of the file in the folder's order, the number of the attachment in its e-mail)


@dataclass
class _Document:
    place: Place                                 # the attachment number is 0 for a file that is not an e-mail
    origin: Path                                 # the file in the folder: the document itself, or the e-mail that carried it
    label: str                                   # the name used in the list of skipped items
    path: Path                                   # where it is read from
    sha256: str
    email_text: str                              # "" for a file that is not an e-mail
    text: str                                    # its first page; "" for an export
    kind: DocClass | Literal["export"] | None    # EXPORT: an export of the ERP; None: nobody could class it


class _Scan:
    """One pass over the folder: the arguments of scan_folder, what has come of the files so far and which files must stay."""

    def __init__(self, folder: Path, store: PoStore, cases_root: Path, settings: Settings, now: datetime, decider: Decider | None):
        self.folder, self.store, self.cases_root, self.settings, self.now, self.decider = folder, store, cases_root, settings, now, decider
        self.outcome = ScanResult()
        self.skips: list[tuple[Place, str, str]] = []    # (place, name, reason)
        self.kept: set[Path] = set()                     # the files that stay in the folder
        self.opened: set[str] = set()                    # the sha256 of the confirmations that a case was opened for in this scan
        self.templates: list[LayoutTemplate] | None = None

    def unpack(self, number: int, path: Path, scratch: Path | None) -> list[_Document]:
        """The documents of a file: the file itself, or the attachments of an e-mail, each classed unless it is a duplicate; none for
        an e-mail filed as an answer to a message of the aid. A file that cannot be opened, and an attachment that cannot be classed,
        stay and are skipped as an error. An export of the ERP is told apart first, before the classes and the duplicate search;
        then, with a PO layout in the settings, a PDF that fits it is classed by the fit, before the cases are searched (see the
        module docstring). The attachments of an e-mail are written into `scratch` (see _unpacking)."""
        try:
            if path.suffix.lower() == ".eml":
                if (case_id := file_answer(self.cases_root, path, self.now, language=self.settings.language)) is not None:
                    self.outcome.replies.append((path.name, case_id))
                    if is_real_reply(path):
                        self.outcome.answered.append(case_id)
                    return []
                email_text, attachments = extract_eml(path, scratch)
                parts = [((number, n), f"{path.name}/{attachment.name}", attachment) for n, attachment in enumerate(attachments, 1)]
                if not parts:
                    self.skip((number, 0), path.name, "no attachment")
            else:
                email_text, parts = "", [((number, 0), path.name, path)]
        except Exception as error:
            self.fail((number, 0), path, path.name, error)
            return []
        layout, columns = self.settings.po_layout, self.settings.export_columns
        documents = []
        for place, label, file in parts:
            try:
                if file.suffix.lower() in EXPORT_SUFFIXES and columns:
                    if is_export(file, columns):
                        documents.append(_Document(place, path, label, file, sha256_file(file), email_text, "", EXPORT))
                    else:
                        self.skip(place, label, NOT_AN_EXPORT)
                    continue
                if file.suffix.lower() not in DOCUMENT_SUFFIXES:
                    self.skip(place, label, f"unsupported file type: {file.suffix.lower() or 'no extension'}")
                    continue
                digest = sha256_file(file)
                kind, fitted = None, False
                if layout is not None and file.suffix.lower() == ".pdf":
                    # With a layout the fit is tested first, on the page as the layout reader reads it: a PDF that fits is a PO PDF,
                    # also when a case holds its bytes, unless its header makes it a confirmation or its e-mail came from another
                    # sender than the PO e-mails do.
                    page = layout_first_page(file)
                    if fits(page, layout) and not titled_confirmation(page, layout):
                        kind, fitted = "po_pdf" if self._po_sender(path) else "confirmation", True
                # The one search of the cases for this file. It comes before the classing, which can cost a decision.
                if kind != "po_pdf" and find_case_by_hash(self.cases_root, digest) is not None:
                    self.skip(place, label, "duplicate")
                    continue
                text = _first_page_text(file)
                if not fitted:
                    kind = _classify(file, text, self.decider, email_text)
                documents.append(_Document(place, path, label, file, digest, email_text, text, kind))
            except Exception as error:
                self.fail(place, path, label, error)
        return documents

    def _po_sender(self, path: Path) -> bool:
        """Whether a PDF that fits the PO layout may be a PO by the sender of this file of the folder: always when the settings name
        no senders, and for a file that is not an e-mail, which has no sender; for an e-mail when its From address is one of the
        senders."""
        senders = self.settings.po_senders
        if not senders or path.suffix.lower() != ".eml":
            return True
        sender = sender_of(path)
        return sender is not None and _sent_by(sender, senders)

    def handle(self, document: _Document) -> None:
        """File the export or the PO, open the case or skip the document, by its class."""
        try:
            if document.kind == EXPORT:
                settings, filed = self.settings, []
                looks = self.store.from_export(document.path, settings.export_columns, settings.export_decimal_comma,
                                               settings.export_fill_down, filed)
                self.outcome.stored_exports.append((document.label, [look.po_number for look in looks], filed))
            elif document.kind == "po_pdf":
                self.outcome.stored_pos.append(self.store.add(document.path, self.settings.po_layout).po_number)
            elif document.kind == "confirmation":
                self._confirmation(document)
            elif document.kind is None and not document.text.strip():
                self.skip(document.place, document.label, "no text layer; class unknown", review=True)
            else:
                self.skip(document.place, document.label, document.kind or "other")
        except Exception as error:
            self.fail(document.place, document.origin, document.label, error)

    def _confirmation(self, document: _Document) -> None:
        if document.sha256 in self.opened:                                       # a copy of a file that this scan has opened a case for
            self.skip(document.place, document.label, "duplicate")
            return
        pattern = self.settings.po_number_pattern
        numbers = list(dict.fromkeys(find_po_numbers(document.email_text, pattern) + find_po_numbers(document.text, pattern)))
        if len(numbers) > 1:
            self.skip(document.place, document.label, "more than one PO number", review=True)
            return
        po_number = numbers[0] if numbers else self._po_by_lines(document)
        self.outcome.new_cases.append(self._open_case(document.path, po_number).case_id)
        self.opened.add(document.sha256)

    def _po_by_lines(self, document: _Document) -> str | None:
        """The one PO that the lines of a quick read of the confirmation point to, or None. A template that does not fit, and a
        file that has no text layer to read, give no lines; that is no error, because the pipeline's reader may still read the file."""
        if not document.text.strip():
            return None
        if self.templates is None:
            self.templates = load_templates()
        template = detect_template(document.path, self.templates)
        if template is None:
            return None
        try:
            reading = read_with_template(document.path, template)
        except ValueError:
            return None
        found = self.store.find_by_lines(reading)
        return found[0] if len(found) == 1 else None

    def _open_case(self, path: Path, po_number: str | None) -> Case:
        """open_case at the time of the scan, or at the first second after it that gives the case an id no case has."""
        for seconds in range(CASE_ID_TRIES):
            try:
                return open_case(self.cases_root, "confirmation", [path], po_number, self.now + timedelta(seconds=seconds))
            except FileExistsError as error:
                taken = error
        raise taken

    def skip(self, place: Place, label: str, reason: str, review: bool = False) -> None:
        """Note that the item is left alone. A review item is written first, so that a failure to write it is an error and not a skip."""
        if review:
            _append_line(self.cases_root / REVIEW_ITEMS_FILE, {"time": self.now.isoformat(), "file": label, "reason": reason})
        self.skips.append((place, label, reason))

    def fail(self, place: Place, origin: Path, label: str, error: Exception) -> None:
        """Note an exception: the file stays where it is, and the item is skipped as an error."""
        self.kept.add(origin)
        self.skip(place, label, f"error: {' '.join(str(error).split()) or type(error).__name__}")

    def file_away(self, files: list[Path]) -> None:
        """Move the files that stay no longer to processed/."""
        for number, path in enumerate(files):
            if path in self.kept:
                continue
            try:
                processed = self.folder / PROCESSED_FOLDER
                processed.mkdir(exist_ok=True)
                path.rename(processed / _free_name(path.name, lambda candidate: (processed / candidate).exists()))
            except OSError as error:
                self.fail((number, 0), path, path.name, error)

    def finish(self) -> ScanResult:
        """The result. What was skipped is listed by file, an e-mail's attachments in their order in the e-mail."""
        self.outcome.skipped = [(label, reason) for _, label, reason in sorted(self.skips, key=lambda skip: skip[0])]
        return self.outcome


def _append_line(path: Path, item: dict) -> None:
    """Add the item as the last JSON line of the file, creating the file and its folder on first use. When the file does not end
    with a newline (a crash cut its last line off), the item starts a line of its own."""
    line = json.dumps(item, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as f:
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                line = b"\n" + line
        f.write(line + b"\n")                    # the file is opened for appending, so this goes to its end
