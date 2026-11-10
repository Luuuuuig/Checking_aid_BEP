"""The review page itself, built with gradio (aid/review/app.py has the view it shows, aid/review/close.py the close it calls).

build_app builds the page and page_rows gives the rows of a list as the page shows them. gradio is imported inside build_app, so that
the rest of the review page works without it. gradio makes an asyncio event loop for each lock it creates outside a running loop
and leaves it open (safe_get_lock of gradio/utils.py, 6.31.0), which the garbage collector later reports as unclosed; build_app
therefore builds the page inside a short-lived event loop of its own, which it closes, and directly when a loop is running already
(a notebook). The page shows no rule code and no error text, and no setting value outside the settings tab: fields, reasons, writes
and failures by their labels in i18n.STRINGS.

The cases the page offers (offered_cases) are those in review and any case with a write to undo, whatever its status: a write made
in the system (status "done") that is not undone, so that a write the aid made on its own can be undone from the page. A change only prepared was not made in
the system and is not offered for undo, as the digest gives it no undo link. The page reads the query of its address when it opens
(selection_from_query): ?case=<case id>, the link of the digest, selects that case when the page offers it, and &undo=<entry id>,
the digest's undo link, also selects that write in the list of writes to undo. Opening a link undoes nothing: the buyer undoes the
write with the undo button. A case the page does not offer, or a write it does not offer for undo, selects nothing. All this is on
the tab "Beoordelen".

The page has three tabs in one gr.Tabs, each with an id: "Beoordelen" (REVIEW_TAB, "beoordelen"), "In Exact invoeren" (hand_list.TAB,
"invoeren") and "Instellingen" (SETTINGS_TAB, "instellingen"). The query tab=invoeren, the link of the digest's line of the changes to
enter (aid/notify.py, list_link), selects the second tab when the page opens; any other value of tab selects nothing.

The tab "In Exact invoeren" lists what the buyer enters in Exact by hand: the open changes of <cases_root>/prepared
(aid/execution/by_hand.py), as aid/review/hand_list.py shows them: the rows of the purchase orders, by order, each named
"Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70", and after them the article prices, each named "artikel KM-0801:
inkoopprijs onbekend → 12,70". Its label is "In Exact invoeren (3)" with the number of open changes, without a number when none is
open, and "In Exact invoeren (?)" while the list cannot be read. Its controls are fixed, none made in a rendered function and every
button outside the rows, so that each handler is one of the page's functions (the tests find them by name): the intro, with under it
the hint of each order above the authorization, "Er is niets in te voeren." when nothing is open and how many lines of the list could
not be read, and "Lijst vernieuwen" beside it; "Aangevinkte regels zijn ingevoerd" and "Aangevinkte regels niet invoeren", which mark
the ticked changes as entered or as dropped (changes_entered, changes_dropped), both above the rows and again below them, so that
they stay in reach of a long list; one group of tick boxes with every open change, one under the other (a style rule given to the
page in the head of an HTML block, for the group's class "hand-rows"), so that the rows of one order stand together; the dropdown
"Blok" with "Alles van dit blok is ingevoerd" (block_entered), which marks the changes of the block chosen as the page showed them
when the list was last read (kept per block in a gr.State that each read fills, with the dropdown's choices), each only while it is
open, so that a change prepared after that read stays on the list; and a note of what a press did ("Vink eerst aan wat u bedoelt."
when nothing is ticked or chosen, and "Er is niets genoteerd: ..." when no ticked row was still open; either way nothing is written).
The list is read again (list_opened) when the page loads, when the tab is selected and with "Lijst vernieuwen", and after each press.
A mark is a line of <cases_root>/prepared/marks.jsonl, at the time of the clock of the page's Deps; nothing else is written, so a
case's result, its write log, its dispositions and prepared_changes.csv stay as they are. A list that cannot be read (another program
holds a file of it, or any other error) leaves the rows as they were, with the label "In Exact invoeren (?)" and the note "Dit is niet
gelukt (<the class of the error>).", as the settings tab says it. On a public share link and without a settings file the tab offers
nothing, as the settings tab does. The review tab's own list of writes to undo is not this list: it offers writes made in a system.

The settings tab ("Instellingen") shows the buyer's settings first, as a form (aid/review/buyer_settings.py has what it shows and
writes): in four groups, each field with its label and a help text that says what the rule does, without a rule code or a
setting's name: "Wat de assistent zelf mag afhandelen" (the two price thresholds, the days a delivery date may pass the supplier's
lead time, the first cases of a supplier that always come to the buyer), "Vragen aan leveranciers" (the wait for an answer, the
name under the messages), "Dagoverzicht en taal" (daily at a time or after every case, the time, the buyer's address, the language)
and "Fiattering" (the approver's address, and a line that the limit itself is set at the installation), under one "Opslaan". Under
it "Leveranciers": a table of every supplier the four supplier settings name, and a form for one supplier, chosen from a dropdown,
which fills the form, or added with its choice "Leverancier toevoegen", with "Leverancier opslaan" and "Leverancier verwijderen".

The form shows the file, and a save writes only what the buyer changed. The eleven fields, the table and the dropdown are filled
from the settings file when the page loads and each time the settings tab is selected (buyer_opened), so that a change made in the
file by hand or from another browser tab shows. Two states of the page keep what the forms were filled with: the eleven texts,
and the supplier form with the supplier it was filled for. "Opslaan" writes the fields whose text differs from the text the form
was filled with, and no other: a field the buyer changed wins over a change made in the file meanwhile, a field he did not touch
is never written and not checked, and with no field changed nothing is written and the file keeps its modification time. A
refused field writes nothing and is named by its label with its problem ("1.000" for a thousand is refused: a percentage and an
amount take two decimals at most). After a save the fields show the values as the file now holds them. A supplier save builds
the four supplier settings from the file as update_settings_file reads it inside its lock (aid/settings.py), so that two saves
pressed at the same moment both land, and changes of a listed supplier only the values the buyer changed; a listed supplier's name
is used as the file has it (also when another browser tab took the supplier out of the file meanwhile, so that the save adds it
again under that name), a typed name trimmed. Three saves of a supplier write nothing and say why: a typed name that is
listed already (the buyer is told to choose that supplier in the list), a new supplier with a name and nothing else, and a listed
one with nothing changed. When the page no longer knows what a form was filled with (the page was started again while a browser
tab stayed open), it fills the form again from the file and writes nothing. The note of a supplier save is no output of the
dropdown's change, which a save causes itself: only the buyer's own choice in the dropdown (its input event) clears it.

When the settings file cannot be read (it is missing, locked, no JSON, or holds a value the settings refuse), the tab shows one
fixed line at its top that says so, naming the fields (a field of the tab by its label, any other setting by its name) and never a
value; the form then shows the settings in use, those the page's own actions run with, and every save of the tab (the main one,
the suppliers', the one of the purchase order's layout, the mailbox's and the reader's) refuses with that line and writes nothing.
The page's own actions (a close, an undo) use the settings a save wrote at once, and the settings of the file whenever the file
changed on disk, looked at by its modification time when the review tab opens or shows a case, before a close or an undo, and each
time the settings tab reads the file (_Live); a file that became invalid keeps the settings in use. The authorization limit, the
folders, the route of the Exact writer (exact_base_url, exact_endpoints, exact_fields), the SMTP server, the tokens and the reserved
precheck_suppliers are not on the tab: they are set at the installation.

Below the buyer's part, folded away under "Koppeling met Exact (bij de installatie)" above the connections, the block of the
connection to Exact (aid/review/exact_settings.py has what it shows and writes; _exact_section builds it). It shows the state of the
connection ("Stand van de koppeling": whether the layout of the purchase order is set, the POs the aid knows and the one filed last,
the files of the watched folder it could not read), read again with "Stand vernieuwen" and each time the tab is selected. Its part 1,
"1. Inkooporders die Exact per e-mail stuurt (pdf)", sets the layout of the PO PDF that Exact e-mails and the senders of those e-mails
(the settings po_layout and po_senders) from one sample PDF of the company's own purchase order. Choosing the sample shows its first
page line by line, the first PAGE_LINES lines with a line under the table when the page has more; "Kopregel zoeken" finds the title row
by one of its words and shows, for each of its words, one of SLOTS dropdowns made once, labelled with the word; "Testen" reads the
sample with the layout of the form and writes nothing; "Opslaan" reads it the same way and writes the two settings with
update_settings_file, or writes nothing and names each problem. Both show the reading below: the header values and the order lines as
two tables, and a line for each problem and each warning of the reader, never the total or the authorization limit; the summary above
the tables only when there is no problem. A sample that cannot be opened is named by a sentence of its own in each of them. The sample
is read where the page library put it when it was chosen, its temporary folder (GRADIO_TEMP_DIR, which the review command sets to
<cases_root>/tmp, aid/review/__main__.py), and is copied nowhere; nothing is filed from it. The block is filled from the settings
file when the page loads and each time the tab is selected, so that it opens with the saved layout and senders (a saved title row of
more than SLOTS words, which the form cannot show, is left out with a line that says so), and keeps what it was filled with: a save
from a form whose two settings the file no longer holds, because another browser tab saved meanwhile, writes nothing and says so.

Below, folded away under "Koppelingen: het Microsoft-postvak en de AI-dienst" (the heading names no installation, because a sign-in
that has lapsed sends the buyer there, as the digest's two lines say), the settings tab connects the work mailbox through the
buyer's own Microsoft sign-in (aid/connections/): the Directory (tenant) ID and the Application (client) ID
of the app registration; "Aanmelden bij Microsoft", which opens Microsoft's sign-in page in a browser window on the PC where the page
runs, as the tab says, and says that it waits for it, and "Aanmelden met een code", which shows a code and an address and then waits
until the code has been entered; the signed-in account, read from the token cache only, so that opening the tab asks Microsoft
nothing, and "Afmelden"; the mailbox (empty: the account's own; another mailbox signs in with the shared scopes); the folder, chosen
from the mailbox's folders once signed in ("Mappen ophalen" lists them again, for another mailbox, and a folder listed for another
mailbox than the one in the field is not saved); "Verbinding testen", which shows how many messages the folder holds and when the
newest arrived, never a subject or a sender, or that the sign-in has lapsed (as "Mappen ophalen" does); how the aid sends (send_via);
and the state of the trigger, from mailbox_state.json (aid/sources/microsoft_mail.py), its times in the local time of the PC.
"Opslaan" validates the six settings and writes them into the settings file with update_settings_file (aid/settings.py), and the
runner takes them over at its next pass (this part is filled when the page loads, and its save gives all six to
update_settings_file, which writes those that differ from the file's); a save that turns the trigger off or names another folder
removes the stored connection (forget_connection), so that a folder connected again later is read from that moment. The tab never
shows a token of the sign-in, and an error by its code ("invalid_grant"), its status ("HTTP 404"), its class, a refused field by its
label, a browser sign-in that timed out and a store the PC refuses by a fixed line, the latter without its folder. On a page opened
on a public share link the tab says only "Niet beschikbaar op een openbare link" and offers nothing, and on a page built without its
settings file (the Colab notebook) it offers nothing either.

The AI section of the settings tab is Microsoft 365 Copilot (aid/readers/copilot_client.py), under a short text of what Copilot
needs: "Aanmelden voor Copilot" signs in in the browser as "Aanmelden bij Microsoft" does, saying first that it waits, with the
permissions of the mailbox in the field (mail_scopes) and of Copilot together; "Copilot testen" sends Copilot one message, "Answer with the word OK only.", and shows "Copilot antwoordt" with
the first 20 characters of the answer, or the error of the client by its text, which names a status and Graph's error code
("Copilot refused: 403 Forbidden") and never a token or a message of Microsoft; and "Documenten lezen met", Copilot or "zonder AI
(parser)", writes the setting reader ("llm" or "parser") into the settings file as soon as it is chosen, as "Opslaan" writes the
six settings, Copilot only once the tenant and the client are saved.

The connections end with "Code voor de uitbreiding in de browser": the code the browser extension sends to the capture server
(capture_token of the settings file), one line that cannot be edited, with a button to copy it, filled from the settings file each
time the page loads, for the buyer to paste into the extension's options. Like the rest of the tab it is on the page of this PC only:
a page on a public share link, or without its settings file, has none.
"""
import asyncio
import io
import re
import threading
import warnings
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, get_args

from msal.oauth2cli.oauth2 import BrowserInteractionTimeoutError

from aid.case import Case, load_case
from aid.connections.microsoft import COPILOT_SCOPES, MicrosoftAccount, SignInFailed, SignInNeeded, mail_scopes
from aid.connections.secrets import SecretStore, default_secrets_dir
from aid.execution.by_hand import FOLDER, changes, mark
from aid.execution.ops import WriteOp
from aid.execution.writer import WriteLog, WriteLogEntry, WriterError
from aid.pipeline import NOT_SENT, Deps
from aid.po_store import PoStore
from aid.readers.copilot_client import CopilotClient, CopilotError
from aid.readers.llm_client import ReaderError
from aid.review import hand_list
from aid.review.app import (DECISIONS_FILE, CaseSummary, Decision, FlagRow, _decisions, _list_cases, _saved_result, decide,
                            load_case_view)
from aid.review.buyer_settings import (BUYER_FIELDS, SUPPLIER_FIELDS, changed_fields, parse, shown, supplier_changes, supplier_names,
                                       supplier_rows, supplier_shown, supplier_update, without_supplier)
from aid.review.close import _records, close_case, undo
from aid.review.exact_settings import FORM as EXACT_FORM
from aid.review.exact_settings import (MEANINGS, PAGE_LINES, SLOTS, SampleUnreadable, Trial, as_setting, form_from_layout, layout_from_form,
                                       line_headers, line_widths, page_lines, reading_shown, saved_values, senders_from, state_lines, title_words,
                                       trial)
from aid.review.i18n import STRINGS, reason_label
from aid.settings import Settings, SettingsInvalid, is_client_id, is_tenant_id, load_settings, read_settings, update_settings_file
from aid.sources.microsoft_mail import GraphError, connection_of, folder_summary, forget_connection, list_folders, trigger_on

_REVIEW_ITEM = re.compile(r"RI\d+")              # the ids of the review items (aid/pipeline.py)
REVIEW_TAB, SETTINGS_TAB = "beoordelen", "instellingen"            # the ids of two tabs; that of "In Exact invoeren" is hand_list.TAB
_ONE_PER_LINE = ".hand-rows .wrap { flex-direction: column; align-items: flex-start; }"    # the tick boxes of that tab, one under the other
SEND_VIA = get_args(Settings.model_fields["send_via"].annotation)     # the values of send_via, in the order the tab offers them
READERS = get_args(Settings.model_fields["reader"].annotation)        # the values of reader: "llm" (Copilot) and "parser"
COPILOT_TEST = "Answer with the word OK only."                         # the message of "Copilot testen"
# The label on the tab of each setting the tab writes, by which a refused field is named, never by its value: the six of the mailbox,
# the reader, the eleven of the buyer's form, the four supplier settings by the heading of the suppliers, the three fields of the
# supplier form (the keys of with_supplier's problems), and the layout of the purchase order and its senders of the block "Koppeling
# met Exact".
FIELD_LABELS = {"ms_tenant_id": "settings_tenant", "ms_client_id": "settings_client", "mailbox": "settings_mailbox",
                "mail_folder": "settings_folder", "mail_folder_name": "settings_folder", "send_via": "settings_send_via",
                "reader": "settings_ai_reader"} | {field: f"settings_field:{field}" for field in BUYER_FIELDS} | {
                field: "settings_suppliers" for field in SUPPLIER_FIELDS} | {
                "supplier_name": "settings_supplier_name", "supplier_email": "settings_supplier_email",
                "supplier_lead_time": "settings_supplier_lead_time"} | {"po_layout": "exact_layout", "po_senders": "exact_field:senders"}
NEW_SUPPLIER = "(new supplier)"                  # the value of "Leverancier toevoegen" in the dropdown of suppliers
# The supplier form as "Leverancier toevoegen" fills it: what a new supplier's values are compared with.
NEW_FORM = {"name": NEW_SUPPLIER, "email": "", "lead_time": "", "review": False, "off": False}
# The groups of the buyer's form, top to bottom, each with its fields in the order of the page.
BUYER_GROUPS = (("handled", ("price_pct", "price_abs", "lead_time_tolerance_days", "first_cases_review")),
                ("questions", ("ask_wait_working_days", "buyer_name")),
                ("digest", ("digest", "digest_time", "buyer_email", "language")),
                ("approval", ("approver_email",)))
_COLUMNS = ("item", "field", "line", "po_value", "doc_value", "reason", "po_source", "doc_source")


def page_rows(rows: list[FlagRow], language: str) -> list[list[str]]:
    """The rows of a list as the page shows them in the language: the item, the field, the line, the two values, the reason and the
    two sources. Fields, reasons and the kinds of matched pairs are shown by their labels in i18n.STRINGS, never as their text, which
    may carry a rule code; a reason without a label shows as "other reason"."""
    strings = STRINGS[language]
    return [[row.item_id, _field(strings, row.field), row.line, _value(strings, row, row.po_value), _value(strings, row, row.doc_value),
             _reason(strings, row.reason), row.po_source, row.doc_source] for row in rows]


def _field(strings: dict[str, str], field: str) -> str:
    return strings.get(f"field:{field}", strings["reason:other"]) if field else ""


def _value(strings: dict[str, str], row: FlagRow, value: str) -> str:
    """A value; the kind of a matched pair (matched, split, merged) by its label."""
    return strings.get(f"pair:{value}", value) if row.field == "line_presence" else value


def _reason(strings: dict[str, str], reason: str) -> str:
    """A reason by its label, or by the label of the reason it begins with (a key ending in " *")."""
    if not reason:
        return ""
    label = reason_label(reason, strings)
    return label if label is not None else strings["reason:other"]


def _write_words(op: WriteOp, strings: dict[str, str]) -> str:
    """A write in words: its kind and its line or article."""
    words = strings[f"kind:{op.kind}"]
    if op.line_id:
        return f"{words}, {strings['line_word']} {op.line_id}"
    return f"{words}, {strings['article_word']} {op.article_code}" if op.article_code else words


def _case_label(summary: CaseSummary, strings: dict[str, str]) -> str:
    """A case in the dropdown: its PO, supplier, time of opening and number of items, and its status when it is not in review."""
    supplier = f" ({summary.supplier})" if summary.supplier else ""
    items = strings["item_one"] if summary.items == 1 else strings["items_many"].format(n=summary.items)
    status = "" if summary.status == "review" else f", {strings[f'status:{summary.status}']}"
    return f"PO {summary.po_number or strings['unknown_po']}{supplier}, {summary.opened_at[:16].replace('T', ' ')}, {items}{status}"


def _describe(row: FlagRow, strings: dict[str, str]) -> str:
    """A review item in one line, above its decision."""
    parts = [f"**{row.item_id}**"]
    if row.field:
        parts.append(_field(strings, row.field))
    if row.line:
        parts.append(f"{strings['line_word']} {row.line}")
    if row.po_value or row.doc_value:
        parts.append(f"{strings['po_value']} {_value(strings, row, row.po_value) or '-'}, {strings['doc_value']} {_value(strings, row, row.doc_value) or '-'}")
    if row.reason:
        parts.append(_reason(strings, row.reason))
    return " | ".join(parts)


def _decision_choices(row: FlagRow, kind: str, strings: dict[str, str]) -> list[tuple[str, str]]:
    """The decisions offered on a row: "ask" only on an item with a finding, and not on a cart (see decide)."""
    asked = bool(row.field) and kind != "cart"
    return [(strings[decision], decision) for decision in get_args(Decision) if decision != "ask" or asked]


def _undoable(case: Case) -> list[WriteLogEntry]:
    """The entries of the write log the page offers to undo (see _made_and_not_undone)."""
    return _made_and_not_undone(case.root)


def _made_and_not_undone(root: Path) -> list[WriteLogEntry]:
    """The entries of the write log of the case folder that were made in the system (status "done"), are not an undo and are not
    undone. undo_entry would also reverse a prepared change, but a change only prepared was not made, and the digest gives it no undo
    link."""
    entries = WriteLog(root).entries()
    undone = {entry.undo_of for entry in entries if entry.undo_of is not None and entry.status != "failed"}
    return [entry for entry in entries if entry.undo_of is None and entry.status == "done" and entry.entry_id not in undone]


def offered_cases(cases_root: Path) -> list[CaseSummary]:
    """The cases the page offers, in the order they were opened: those in review, and any case with a write to undo
    (_made_and_not_undone), whatever its status (closed, waiting, failed). A case whose write log cannot be read is offered only
    when it is in review."""
    return _list_cases(cases_root, lambda root, result: result.status == "review" or bool(_readable_undoable(root)))


def _readable_undoable(root: Path) -> list[WriteLogEntry]:
    """_made_and_not_undone, or none when the write log cannot be read: an unreadable log offers no undo."""
    try:
        return _made_and_not_undone(root)
    except (OSError, ValueError):
        return []


def selection_from_query(cases_root: Path, params: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The case and the write the query of the page's address selects (see the module docstring): (case id, entry id). The case is
    the parameter "case" when the page offers it (a case in review, or any case with a write to undo), else None; the entry is the
    parameter "undo" when it is a write of that case that the page offers to undo, else None (also when the case's write log
    cannot be read). Nothing is undone."""
    case_id, entry_id = params.get("case"), params.get("undo")
    if not case_id or case_id not in {summary.case_id for summary in offered_cases(cases_root)}:
        return None, None
    if not entry_id or entry_id not in {entry.entry_id for entry in _readable_undoable(Path(cases_root) / case_id)}:
        return case_id, None
    return case_id, entry_id


def _undo_choices(case: Case, strings: dict[str, str]) -> list[tuple[str, str]]:
    choices = []
    for entry in _undoable(case):
        values = f": {entry.op.old_value or '-'} -> {entry.op.new_value or '-'}" if entry.op.old_value or entry.op.new_value else ""
        choices.append((f"{_write_words(entry.op, strings)}{values}", entry.entry_id))
    return choices


def close_text(case: Case, deps: Deps, language: str) -> tuple[str, bool]:
    """Close the case and say how it went, in the language, and whether the case is still open (in review). A write that did not go
    through and a message not sent are named by fixed labels: the error's own text, which may name an endpoint or a folder of the
    settings, stays in the result, step 09 and the audit record."""
    strings = STRINGS[language]
    try:
        result = close_case(case, deps)
    except ValueError:
        return strings["not_closed"], False
    text = strings["new_status"].format(status=strings[f"status:{result.status}"])
    record = _records(case)[-1]
    if (stop := record["stop"]) is not None:
        error = _reason(strings, stop["message"]) if stop["waiting"] else strings["write_refused"]
        text += " " + strings["write_stopped"].format(write=_write_words(WriteOp.model_validate(stop["write"]), strings), error=error)
    if any(message["status"].startswith("not sent (") and message["status"] != NOT_SENT for message in record["messages"]):
        text += " " + strings["message_not_sent"]
    if record.get("question_open"):
        text += f" {strings['question_open']}."
    return text, result.status == "review"


def undo_text(case: Case, entry_id: str, deps: Deps, language: str) -> str:
    """Undo one write of the case and say how it went, in the language; a WriterError by a fixed label, as close_text does."""
    strings = STRINGS[language]
    entry = next((entry for entry in WriteLog(case.root).entries() if entry.entry_id == entry_id), None)
    try:
        made = undo(case, entry_id, deps)
    except WriterError:
        return strings["undo_failed"]
    return strings["undone"].format(write=_write_words(entry.op, strings)) if made is not None else strings["not_undone"]


def _article_items(case: Case) -> set[str]:
    """The review items whose waiting write is an article price, whose article box the page ticks."""
    kinds = {entry.entry_id: entry.op.kind for entry in WriteLog(case.root).entries()}
    return {item.item_id for item in _saved_result(case).review_items if item.entry_id and kinds.get(item.entry_id) == "article_price"}


# --- the page ---

def import_gradio():
    """gradio, imported with the DeprecationWarning and PendingDeprecationWarning that third-party packages raise during the import
    ignored, so that the suite stays clean under -W error (the aid's own warnings are not ignored). gradio 6.31.0 raises none."""
    with warnings.catch_warnings():
        for category in (DeprecationWarning, PendingDeprecationWarning):
            warnings.filterwarnings("ignore", category=category, module=r"(?!aid(\.|$))")
        import gradio
    return gradio


class _Live:
    """The Deps the page's own actions use: those the page was built with, carrying the settings of its settings file once that file
    changed. now() looks at the file's modification time and reads a changed file again. A file that cannot be read keeps the
    settings in use: one that does not validate is not read again until it changes once more, and one that cannot be opened (missing,
    or held by another program for a moment) is tried again at the next look. take() puts the settings a save of the settings tab
    wrote into the Deps at once, and settings() gives the settings in use, which the settings tab shows when its file cannot be
    read. The Deps the page was given are never changed: a new copy carries the new settings."""

    def __init__(self, deps: Deps, file: Path | None):
        self.deps, self.file, self.modified = deps, file, _modified(file)
        self.lock = threading.Lock()

    def now(self) -> Deps:
        with self.lock:
            modified = _modified(self.file)
            if self.file is not None and modified != self.modified:
                try:
                    self.deps = self.deps.model_copy(update={"settings": load_settings(self.file)})
                except OSError:                  # not opened at this look: the settings in use are kept, and the next look tries again
                    return self.deps
                except ValueError:               # a file that does not validate: the settings in use are kept
                    pass
                self.modified = modified
            return self.deps

    def take(self, settings: Settings) -> None:
        with self.lock:
            self.deps, self.modified = self.deps.model_copy(update={"settings": settings}), _modified(self.file)

    def settings(self) -> Settings:
        """The settings in use: those the page's own actions run with now."""
        return self.now().settings


class _Refused(Exception):
    """A save that its handler refuses while update_settings_file holds its lock, with the note the page then shows; nothing is
    written."""

    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


def _modified(path: Path | None) -> int | None:
    """The modification time of the file in nanoseconds; None when there is no file, or it cannot be looked at."""
    try:
        return path.stat().st_mtime_ns if path is not None else None
    except OSError:
        return None


class _Tab(NamedTuple):
    """What the settings tab is built with (see build_app)."""
    file: Path | None                            # the settings file it writes; None: the tab offers nothing
    share: bool                                  # the page is opened on a public share link: the tab offers nothing
    account_factory: Callable[[str, str], MicrosoftAccount]
    session: object                              # the requests session that Graph is called through; None: requests


def build_app(deps: Deps, settings_file: Path | None = None, share: bool = False,
              account_factory: Callable[[str, str], MicrosoftAccount] | None = None, session=None) -> "gr.Blocks":  # noqa: F821
    """The review page, Dutch by default with a switch to English, in three tabs. On "Beoordelen": a dropdown of the cases offered
    (offered_cases), with the case and the write of the address's query selected when the page opens (selection_from_query); the
    supplier's document and the PO version as image galleries; the three lists as tables, with the four decisions on each review
    item and the tick for the article price on a price; the banner when a change sends the PO through approval again; the close and
    undo buttons. On "In Exact invoeren", selected when the query holds tab=invoeren: the list of the changes to enter in Exact, to
    tick as entered or as not to enter (see the module docstring). On "Instellingen": the buyer's settings and the suppliers, then,
    folded away, the connection to Exact and the settings of the work mailbox (see the module docstring), written into settings_file.
    The last two tabs show nothing but a line when the page is opened on a public link (share) or without a settings file.
    account_factory gives the Microsoft account of a tenant and a client (by default with the token cache of the store in
    default_secrets_dir(); the tests give a fake one), and session is the requests session Graph is called through. No rule code is
    shown, and no setting value outside the settings tab. Built inside an event loop of its own (see the module docstring). The
    connections end with the AI section (see the module docstring), whose Copilot is called through the same session."""
    gr = import_gradio()
    tab = _Tab(Path(settings_file) if settings_file is not None else None, share, account_factory or _account, session)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_built(gr, deps, tab))
        finally:
            loop.close()
    return _page(gr, deps, tab)


async def _built(gr, deps: Deps, tab: _Tab):
    return _page(gr, deps, tab)


def _page(gr, deps: Deps, tab: _Tab):
    from PIL import Image

    cases_root, first = deps.settings.cases_root, STRINGS["nl"]
    empty = {"flags": [], "matches": [], "uncompared": []}
    live = _Live(deps, tab.file)                 # the Deps of the page's actions, with the settings of the file as it is now

    def choices(language: str) -> list[tuple[str, str]]:
        return [(_case_label(summary, STRINGS[language]), summary.case_id) for summary in offered_cases(cases_root)]

    def tables(lists: dict, language: str) -> list[dict]:
        headers = [STRINGS[language][column] for column in _COLUMNS]
        return [{"headers": headers, "data": page_rows(lists[name], language)} for name in ("flags", "matches", "uncompared")]

    def opened(language: str, request: gr.Request) -> tuple:
        """The page opened: the cases offered, with the case of the query selected, the write of the query to select among the
        writes to undo once the case is shown (selection_from_query), and the tab "In Exact invoeren" selected when the query holds
        tab=invoeren, the link of the digest (aid/notify.py, list_link)."""
        live.now()
        params = getattr(request, "query_params", None) or {}
        case_id, entry_id = selection_from_query(cases_root, params)
        selected = gr.update(selected=hand_list.TAB) if params.get("tab") == hand_list.TAB else gr.update()
        return gr.update(choices=choices(language), value=case_id), entry_id, selected

    def show(case_id: str | None, language: str, wanted: str | None = None) -> tuple:
        """The case chosen: the banner, the documents, the lists, the writes to undo (with the write wanted selected when it is
        among them), the state the decisions are shown from, and a message ("" when there is none). A decisions file that cannot be
        read empties the view and says so."""
        nothing = (gr.update(visible=False), [], [], *tables(empty, language), gr.update(choices=[], value=None), None)
        live.now()
        if not case_id:
            return (*nothing, "")
        case = load_case(cases_root, case_id)
        try:
            view = load_case_view(case, live.now().store)       # the store as it is now: a moved folder keeps its PO pages
        except ValueError:                          # a damaged decisions.jsonl (_decisions)
            return (*nothing, STRINGS[language]["decisions_damaged"].format(file=DECISIONS_FILE))
        lists = {"flags": view.flags, "matches": view.matches, "uncompared": view.uncompared}
        state = {"case_id": case_id, "kind": case.kind, "lists": lists, "decisions": _decisions(case), "articles": _article_items(case)}
        images = [[Image.open(io.BytesIO(page)) for page in pages] for pages in (view.doc_pages, view.po_pages)]
        undo_choices = _undo_choices(case, STRINGS[language])
        selected = wanted if wanted in [entry_id for _, entry_id in undo_choices] else None
        return (gr.update(visible=view.reapproval_banner), *images, *tables(lists, language),
                gr.update(choices=undo_choices, value=selected), state, "")

    def relabel(language: str, case_id: str | None, state: dict | None) -> tuple:
        strings = STRINGS[language]
        flags, matches, uncompared = tables(state["lists"] if state else empty, language)
        undo_choices = _undo_choices(load_case(cases_root, case_id), strings) if case_id else []
        return (f"# {strings['title']}", gr.update(label=strings["language"]), gr.update(label=strings["case"], choices=choices(language)),
                gr.update(value=strings["refresh"]), f"**{strings['banner']}**", gr.update(label=strings["document"]),
                gr.update(label=strings["po_document"]), f"### {strings['flags']}", flags, f"### {strings['decisions']}",
                f"### {strings['matches']}", matches, f"### {strings['uncompared']}", uncompared, gr.update(value=strings["close"]),
                gr.update(label=strings["undo_choice"], choices=undo_choices, value=None), gr.update(value=strings["undo"]))

    def saver(case_id: str, item_id: str) -> Callable:
        def save(decision: str | None, article: bool, language: str, current: dict | None):
            if not decision:
                return gr.update()
            try:
                decide(load_case(cases_root, case_id), item_id, decision, bool(article))
            except ValueError:
                return STRINGS[language]["refused"]
            if current and current["case_id"] == case_id:      # the state the radios are shown from follows the decision
                current["decisions"][item_id] = {"item_id": item_id, "decision": decision, "article_update": bool(article)}
            return STRINGS[language]["saved"].format(item=item_id)
        return save

    def close(case_id: str | None, language: str) -> tuple:
        if not case_id:
            return STRINGS[language]["no_case"], gr.update()
        text, still_open = close_text(load_case(cases_root, case_id), live.now(), language)
        return text, gr.update(choices=choices(language), value=case_id if still_open else None)

    def do_undo(case_id: str | None, entry_id: str | None, language: str) -> tuple:
        if not case_id or not entry_id:
            return STRINGS[language]["choose_write"], gr.update()
        case = load_case(cases_root, case_id)
        return undo_text(case, entry_id, live.now(), language), gr.update(choices=_undo_choices(case, STRINGS[language]), value=None)

    # delete_cache: every hour the page library removes the temporary files of this page that are older than an hour (an uploaded
    # file, the page images of a document), so a file goes between one and two hours after it was made, and all of them at a stop
    # with Ctrl-C in the page's own window. The launcher ends the page with terminate, which leaves them: the page's next start
    # empties <cases_root>/tmp, where they lie (aid/review/__main__.py).
    with gr.Blocks(analytics_enabled=False, title=first["title"], delete_cache=(3600, 3600)) as app:
        state = gr.State(None)
        wanted = gr.State(None)                      # the write of the query to undo, selected when its case is shown
        with gr.Row():
            title = gr.Markdown(f"# {first['title']}")
            language = gr.Radio(choices=[("Nederlands", "nl"), ("English", "en")], value="nl", label=first["language"])
        with gr.Tabs() as tabs:
            with gr.Tab(first["review_tab"], id=REVIEW_TAB) as review_tab:
                with gr.Row():
                    case_choice = gr.Dropdown(choices=choices("nl"), value=None, label=first["case"], scale=4)
                    refresh = gr.Button(first["refresh"], scale=1)
                banner = gr.Markdown(f"**{first['banner']}**", visible=False)
                with gr.Row():
                    doc_gallery = gr.Gallery(label=first["document"], columns=1, height=700, object_fit="contain")
                    po_gallery = gr.Gallery(label=first["po_document"], columns=1, height=700, object_fit="contain")
                flags_title = gr.Markdown(f"### {first['flags']}")
                flags_table = gr.Dataframe(value=tables(empty, "nl")[0], interactive=False, wrap=True)
                decisions_title = gr.Markdown(f"### {first['decisions']}")

                @gr.render(inputs=[state, language])
                def decision_rows(current: dict | None, language_now: str):
                    if not current:
                        return
                    strings = STRINGS[language_now]
                    rows = [row for name in ("flags", "uncompared") for row in current["lists"][name] if _REVIEW_ITEM.fullmatch(row.item_id)]
                    for row in sorted(rows, key=lambda row: int(row.item_id[2:])):
                        chosen = current["decisions"].get(row.item_id, {})
                        with gr.Row():
                            gr.Markdown(_describe(row, strings))
                            radio = gr.Radio(choices=_decision_choices(row, current["kind"], strings), value=chosen.get("decision"),
                                             label=strings["decision"], key=f"decision-{current['case_id']}-{row.item_id}")
                            tick = gr.Checkbox(value=chosen.get("article_update", row.item_id in current["articles"]), label=strings["article_update"], visible=row.field == "price",
                                               key=f"article-{current['case_id']}-{row.item_id}")
                        save = saver(current["case_id"], row.item_id)
                        radio.input(save, inputs=[radio, tick, language, state], outputs=status)
                        tick.input(save, inputs=[radio, tick, language, state], outputs=status)

                matches_title = gr.Markdown(f"### {first['matches']}")
                matches_table = gr.Dataframe(value=tables(empty, "nl")[1], interactive=False, wrap=True)
                uncompared_title = gr.Markdown(f"### {first['uncompared']}")
                uncompared_table = gr.Dataframe(value=tables(empty, "nl")[2], interactive=False, wrap=True)
                close_button = gr.Button(first["close"], variant="primary")
                status = gr.Markdown("")
                with gr.Row():
                    undo_choice = gr.Dropdown(choices=[], value=None, label=first["undo_choice"], scale=4)
                    undo_button = gr.Button(first["undo"], scale=1)
            with gr.Tab(first["hand_tab"], id=hand_list.TAB) as hand_tab:
                _hand_tab(gr, app, language, hand_tab, live, tab, cases_root)
            with gr.Tab(first["settings_tab"], id=SETTINGS_TAB) as settings_tab:
                _settings_tab(gr, app, language, settings_tab, live, tab)

        view = [banner, doc_gallery, po_gallery, flags_table, matches_table, uncompared_table, undo_choice, state]
        app.load(opened, inputs=language, outputs=[case_choice, wanted, tabs])
        refresh.click(lambda language_now: gr.update(choices=choices(language_now)), inputs=language, outputs=case_choice)
        case_choice.change(show, inputs=[case_choice, language, wanted], outputs=[*view, status])
        language.change(relabel, inputs=[language, case_choice, state],
                        outputs=[title, language, case_choice, refresh, banner, doc_gallery, po_gallery, flags_title, flags_table, decisions_title,
                                 matches_title, matches_table, uncompared_title, uncompared_table, close_button, undo_choice, undo_button])
        close_button.click(close, inputs=[case_choice, language], outputs=[status, case_choice]).then(
            lambda case_id, language_now: show(case_id, language_now)[:-1], inputs=[case_choice, language], outputs=view)
        undo_button.click(do_undo, inputs=[case_choice, undo_choice, language], outputs=[status, undo_choice])
        language.change(lambda language_now: (gr.update(label=STRINGS[language_now]["review_tab"]),
                                               gr.update(label=STRINGS[language_now]["settings_tab"])),
                        inputs=language, outputs=[review_tab, settings_tab])
    return app


# --- the tab "In Exact invoeren" ---

def _hand_tab(gr, app, language, hand_tab, live: _Live, tab: _Tab, cases_root: Path) -> None:
    """The content of the tab "In Exact invoeren" and its events, inside the tab (see the module docstring): the intro with the lines
    under it and "Lijst vernieuwen", the two buttons for the ticked rows, one group of tick boxes with every open change, one under the
    other, the two buttons again, the dropdown of the blocks with its button, and a note. hand_tab is the tab itself, whose label
    counts the open changes and whose selection reads the list again. The list is read from <cases_root>/prepared
    (aid/execution/by_hand.py) and shown as aid/review/hand_list.py makes it; the rows of each block as the last read showed them are
    kept in a gr.State, {block id: [the values of its rows]}, for the block's button. A mark carries the time of the clock of the
    page's Deps. On a public share link and without a settings file the tab says only that it is not available, as the settings tab
    does, and offers nothing."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: (STRINGS[language_now][key], gr.update(label=STRINGS[language_now]["hand_tab"])),
                        inputs=language, outputs=[notice, hand_tab])
        return
    prepared = Path(cases_root) / FOLDER

    def shown(language_now: str, note: str = "", ticked: list[str] | None = None, chosen: str | None = None) -> tuple:
        """The tab as the list is now: its label with the number of open changes, the intro with the lines under it, the tick boxes
        with the rows ticked that are still listed, the dropdown of the blocks with the block chosen while it is listed, the note,
        and the rows of each block as this read shows them, for the block's button."""
        strings = STRINGS[language_now]
        found, passed = changes(prepared)
        open_ = [change for change in found if change.state == "open"]
        listed = hand_list.blocks(open_, live.now().store, cases_root, language_now)
        rows, offered = hand_list.tick_rows(listed), hand_list.block_choices(listed)
        label = strings["hand_tab_count"].format(n=len(open_)) if open_ else strings["hand_tab"]
        kept = [value for value in ticked or [] if value in {value for _, value in rows}]
        chosen = chosen if chosen in {block_id for _, block_id in offered} else None
        return (gr.update(label=label), hand_list.intro(listed, passed, language_now), gr.update(choices=rows, value=kept),
                gr.update(choices=offered, value=chosen, label=strings["hand_block"]), note,
                {block_id: [value for _, value in block_rows] for block_id, _, block_rows in listed})

    def marked(values: list[str], what: str, language_now: str) -> tuple:
        """Mark the change of each row's value, by its entry ids, and show the list again with the note of how many changes were
        marked. A change that is no longer open (another browser tab marked it, or a newer document replaced it) is not marked and not
        counted; when none was, the note says that nothing was noted and why."""
        now = live.now().clock()
        done = sum(1 for value in values if mark(prepared, hand_list.ids_of(value), what, now) > 0)
        strings = STRINGS[language_now]
        if not done:
            return shown(language_now, strings["hand_nothing_noted"])
        return shown(language_now, strings[f"hand_{what}_one" if done == 1 else f"hand_{what}_many"].format(n=done))

    def unread(error: Exception, language_now: str) -> tuple:
        """A list that cannot be read (a file of it cannot be opened at all, or any other error): the rows as they were, the label
        "In Exact invoeren (?)", and the note that names the class of the error."""
        strings = STRINGS[language_now]
        return (gr.update(label=strings["hand_tab_unread"]), gr.update(), gr.update(), gr.update(),
                strings["settings_failed"].format(code=type(error).__name__), gr.update())

    def list_opened(language_now: str) -> tuple:
        """The page loaded, the tab selected, or "Lijst vernieuwen" pressed: the list read again."""
        try:
            return shown(language_now)
        except Exception as error:
            return unread(error, language_now)

    def changes_entered(ticked: list[str] | None, language_now: str) -> tuple:
        """"Aangevinkte regels zijn ingevoerd", above or below the rows: the ticked changes marked as entered in Exact; with nothing
        ticked nothing is written."""
        try:
            return marked(ticked, "entered", language_now) if ticked else shown(language_now, STRINGS[language_now]["hand_tick_first"])
        except Exception as error:
            return unread(error, language_now)

    def changes_dropped(ticked: list[str] | None, language_now: str) -> tuple:
        """"Aangevinkte regels niet invoeren", above or below the rows: the ticked changes marked as dropped, so that the aid no longer
        asks for them; with nothing ticked nothing is written."""
        try:
            return marked(ticked, "dropped", language_now) if ticked else shown(language_now, STRINGS[language_now]["hand_tick_first"])
        except Exception as error:
            return unread(error, language_now)

    def block_entered(chosen: str | None, seen: dict[str, list[str]] | None, language_now: str) -> tuple:
        """"Alles van dit blok is ingevoerd": the changes of the chosen block as the page showed them when the list was last read
        (seen) marked as entered, each only while it is open, so that a change prepared after that read stays on the list; with no
        block chosen nothing is written."""
        try:
            if not chosen:
                return shown(language_now, STRINGS[language_now]["hand_tick_first"])
            return marked((seen or {}).get(chosen, []), "entered", language_now)
        except Exception as error:
            return unread(error, language_now)

    def hand_relabel(language_now: str, ticked: list[str] | None, chosen: str | None) -> tuple:
        """The page's language switched: the tab in that language, with the rows ticked and the block chosen kept while they are
        listed, and the note cleared."""
        strings = STRINGS[language_now]
        try:
            listed = shown(language_now, "", ticked, chosen)
        except Exception as error:
            listed = unread(error, language_now)
        buttons = ("refresh", "hand_entered", "hand_dropped", "hand_entered", "hand_dropped", "hand_block_entered")
        return (*listed, *(gr.update(value=strings[key]) for key in buttons))      # an update each: gradio takes its value out as it applies it

    with gr.Row():                                   # in columns, whose scale a row keeps, as it does not keep a Markdown's
        with gr.Column(scale=4):
            intro = gr.Markdown(first["hand_intro"], elem_id="hand-intro")
        with gr.Column(scale=1, min_width=160):
            refresh = gr.Button(first["refresh"])
    with gr.Row():
        entered_above = gr.Button(first["hand_entered"], variant="primary")
        dropped_above = gr.Button(first["hand_dropped"])
    rows = gr.CheckboxGroup(choices=[], value=[], show_label=False, elem_id="hand-rows", elem_classes=["hand-rows"])
    with gr.Row():
        entered = gr.Button(first["hand_entered"], variant="primary")
        dropped = gr.Button(first["hand_dropped"])
    with gr.Row():
        block = gr.Dropdown(choices=[], value=None, label=first["hand_block"], scale=4)
        block_button = gr.Button(first["hand_block_entered"], scale=1)
    note = gr.Markdown("", elem_id="hand-note")
    gr.HTML("", head=f"<style>{_ONE_PER_LINE}</style>", container=False, padding=False, js_on_load=None)   # last, so its space shows nowhere
    seen = gr.State({})                              # {block id: [the values of its rows]} as the last read of the list showed them

    listed = [hand_tab, intro, rows, block, note, seen]
    gr.on(triggers=[app.load, hand_tab.select, refresh.click], fn=list_opened, inputs=language, outputs=listed)
    gr.on(triggers=[entered_above.click, entered.click], fn=changes_entered, inputs=[rows, language], outputs=listed)
    gr.on(triggers=[dropped_above.click, dropped.click], fn=changes_dropped, inputs=[rows, language], outputs=listed)
    block_button.click(block_entered, inputs=[block, seen, language], outputs=listed)
    language.change(hand_relabel, inputs=[language, rows, block],
                    outputs=[*listed, refresh, entered_above, dropped_above, entered, dropped, block_button])


# --- the settings tab ---

def _settings_tab(gr, app, language, settings_tab, live: _Live, tab: _Tab) -> None:
    """The content of the settings tab and its events, inside the tab (see the module docstring): the buyer's settings and the
    suppliers (_buyer_section), then folded away the block of the connection to Exact (_exact_section) and the connections.
    settings_tab is the tab itself, whose selection fills the buyer's part again; live holds the Deps of the page's own actions: its
    clock is the block's time, and its settings are those the tab shows when its file cannot be read,
    and it is given the settings each save wrote, so that the page's own actions use them at once. The folders listed are kept as
    {"mailbox": the mailbox they were listed for, "folders": id -> display name}, so that a folder is never saved with another
    mailbox's address."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: STRINGS[language_now][key], inputs=language, outputs=notice)
        return
    accounts: dict[tuple[str, str], MicrosoftAccount] = {}

    def unreadable(fields: list[str], strings: dict[str, str]) -> str:
        """The one line of a settings file that cannot be read, naming the fields at fault by their labels and never a value."""
        named = _labels([field for field in fields if field != "the file"], strings)
        return strings["settings_file_unreadable"].format(fields=strings["settings_file_fields"].format(fields=named) if named else "")

    def read(strings: dict[str, str]) -> tuple[Settings, str | None]:
        """The settings the file holds, and None. When the file cannot be read (it is missing, locked, no JSON, or holds a value
        the settings refuse): the settings in use, those the page's own actions run with, and the line that says so. A file the
        tab read is also the page's own actions' from then on (live.now()), so that the settings in use are those of the last file
        the page could read, never those of the page's start."""
        try:
            settings = read_settings(tab.file)
        except SettingsInvalid as error:
            return live.settings(), unreadable(error.fields, strings)
        except (OSError, ValueError):
            return live.settings(), unreadable([], strings)
        live.now()
        return settings, None

    def write(build: Callable[[Settings], Mapping[str, object]], strings: dict[str, str]) -> tuple[Settings | None, str | None, str | None]:
        """Write into the settings file what build makes of the settings the file holds, read inside the lock of
        update_settings_file, so that a save never builds on a file another save is changing: (the settings the file then holds,
        None, None). A save that is refused writes nothing and gives (None, the note, the line of a file that cannot be read or
        None): the line when the file as it is cannot be read, the note of a _Refused that build raised, the labels of the fields
        whose new values the settings refuse (never a value), or the class of the error of a write that failed. The settings
        written are handed to the page's own actions at once."""
        reached = []                                 # whether the file was read and build was given its settings

        def values(held: Settings) -> Mapping[str, object]:
            reached.append(held)
            return build(held)

        try:
            saved = update_settings_file(tab.file, values)
        except _Refused as refused:
            return None, refused.note, None
        except SettingsInvalid as error:
            if not reached:                          # the file as it is: by the labels, never a value
                return None, (line := unreadable(error.fields, strings)), line
            return None, strings["settings_not_saved"].format(fields=_labels(error.fields, strings)), None
        except (OSError, ValueError) as error:
            if not reached:
                return None, (line := unreadable([], strings)), line
            return None, strings["settings_failed"].format(code=type(error).__name__), None
        live.take(saved)
        return saved, None, None

    def account_of(tenant: str | None, client: str | None) -> MicrosoftAccount | None:
        """The account of the tenant and the client, made once for the page; None when they are no valid IDs. Raises when the
        account cannot be made (its store is refused: a folder inside a git repository)."""
        tenant, client = (tenant or "").strip(), (client or "").strip()
        if not (is_tenant_id(tenant) and is_client_id(client)):
            return None
        if (tenant, client) not in accounts:
            accounts[(tenant, client)] = tab.account_factory(tenant, client)
        return accounts[(tenant, client)]

    def known(tenant: str | None, client: str | None) -> MicrosoftAccount | None:
        """account_of for the line that names the account; None also when the account cannot be made."""
        try:
            return account_of(tenant, client)
        except Exception:
            return None

    def begin(tenant: str, client: str, strings: dict[str, str]) -> tuple[MicrosoftAccount | None, str | None]:
        """The account for an action, or None with the note that says why there is none."""
        try:
            account = account_of(tenant, client)
        except Exception:                            # the store refused, by a fixed label: its text names the folder
            return None, strings["settings_store_refused"]
        return (account, None) if account is not None else (None, strings["settings_ids_needed"])

    def current() -> Settings:
        """The settings in the file, or the settings in use when the file cannot be read."""
        return read(first)[0]

    def folders_shown(account: MicrosoftAccount, mailbox: str | None, strings: dict[str, str]) -> tuple:
        """The folders of the mailbox for the folder list, with the folder saved selected when it is among them: (the list, the
        folders listed, a note)."""
        mailbox = (mailbox or "").strip()
        try:
            listed = list_folders(account, mailbox or None, tab.session)
        except SignInNeeded:
            return gr.update(), gr.update(), _no_token_text(account, strings)
        except GraphError as error:
            return gr.update(), gr.update(), strings["settings_graph_failed"].format(code=error)
        folders, saved = {folder: name for name, folder in listed}, current().mail_folder
        return (gr.update(choices=listed, value=saved if saved in folders else None), {"mailbox": mailbox, "folders": folders},
                strings["settings_folders_loaded"].format(count=len(listed)))

    def settings_opened(language_now: str) -> tuple:
        """The tab opened: the six settings of the file, the signed-in account and the state of the trigger."""
        strings, settings = STRINGS[language_now], current()
        folders = {settings.mail_folder: settings.mail_folder_name or settings.mail_folder} if settings.mail_folder else {}
        account = known(settings.ms_tenant_id, settings.ms_client_id)
        return (settings.ms_tenant_id or "", settings.ms_client_id or "", settings.mailbox or "",
                gr.update(choices=[(name, folder) for folder, name in folders.items()], value=settings.mail_folder), settings.send_via,
                _account_text(account, strings), _trigger_text(settings, strings),
                {"mailbox": settings.mailbox or "", "folders": folders})

    def sign_in_browser(tenant: str, client: str, mailbox: str, language_now: str):
        """Sign in in the browser of this PC, saying first that the page waits for it, then list the folders: (the account, the
        folder list, the folders listed, a note)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings)
        if refused:
            yield gr.update(), gr.update(), gr.update(), refused
            return
        yield gr.update(), gr.update(), gr.update(), strings["settings_waiting"]
        try:
            account.sign_in_browser(mail_scopes((mailbox or "").strip() or None))
        except SignInFailed as error:
            yield _account_text(account, strings), gr.update(), gr.update(), strings["settings_sign_in_failed"].format(code=error)
            return
        except BrowserInteractionTimeoutError:       # nobody signed in within BROWSER_SECONDS, or no window opened
            yield gr.update(), gr.update(), gr.update(), strings["settings_browser_timeout"]
            return
        except Exception as error:
            yield gr.update(), gr.update(), gr.update(), strings["settings_failed"].format(code=type(error).__name__)
            return
        yield (_account_text(account, strings), *folders_shown(account, mailbox, strings))

    def sign_in_code(tenant: str, client: str, mailbox: str, language_now: str):
        """The device sign-in: first the code and the address, then, once the code has been entered, as sign_in_browser."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings)
        if refused:
            yield gr.update(), gr.update(), gr.update(), refused
            return
        try:
            flow = account.start_device_sign_in(mail_scopes((mailbox or "").strip() or None))
            yield gr.update(), gr.update(), gr.update(), strings["settings_code"].format(address=flow["verification_uri"], code=flow["user_code"])
            account.finish_device_sign_in(flow)
        except SignInFailed as error:
            yield _account_text(account, strings), gr.update(), gr.update(), strings["settings_sign_in_failed"].format(code=error)
            return
        except Exception as error:
            yield gr.update(), gr.update(), gr.update(), strings["settings_failed"].format(code=type(error).__name__)
            return
        yield (_account_text(account, strings), *folders_shown(account, mailbox, strings))

    def sign_out(tenant: str, client: str, language_now: str) -> tuple:
        """Remove the account and its tokens from this PC: (the account, the folder list, the folders listed, a note)."""
        strings = STRINGS[language_now]
        try:
            account = account_of(tenant, client)
        except Exception:
            return gr.update(), gr.update(), gr.update(), strings["settings_store_refused"]
        try:
            if account is not None:
                account.sign_out()
        except Exception as error:
            return gr.update(), gr.update(), gr.update(), strings["settings_failed"].format(code=type(error).__name__)
        return strings["settings_not_signed_in"], gr.update(choices=[], value=None), {"mailbox": None, "folders": {}}, strings["settings_signed_out"]

    def load_folders(tenant: str, client: str, mailbox: str, language_now: str) -> tuple:
        """List the folders again, of the mailbox now in the field: (the folder list, the folders listed, a note)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings)
        if refused:
            return gr.update(), gr.update(), refused
        return folders_shown(account, mailbox, strings)

    def check_connection(tenant: str, client: str, mailbox: str, folder: str | None, language_now: str) -> str:
        """How many messages the folder holds and when the newest arrived; never a subject or a sender."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant, client, strings)
        if refused:
            return refused
        if not folder:
            return strings["settings_folder_needed"]
        try:
            count, newest = folder_summary(account, (mailbox or "").strip() or None, folder, tab.session)
        except SignInNeeded:
            return _no_token_text(account, strings)
        except GraphError as error:
            return strings["settings_graph_failed"].format(code=error)
        return strings["settings_tested"].format(count=count, newest=_when(newest)) if newest else strings["settings_tested_empty"]

    def save_settings(tenant: str, client: str, mailbox: str, folder: str | None, send_via: str, listed: dict, language_now: str) -> tuple:
        """Validate the six settings and write them into the settings file: (a note, the state of the trigger). A folder listed for
        another mailbox than the one in the field is not saved, and nothing is saved into a file that cannot be read: the note is
        then the line that says so."""
        strings, listed = STRINGS[language_now], listed or {}
        before, line = read(strings)
        if line is not None:
            return line, gr.update()
        given = {"ms_tenant_id": tenant, "ms_client_id": client, "mailbox": mailbox, "mail_folder": folder, "mail_folder_name": None,
                 "send_via": send_via}
        values = {key: (value.strip() or None) if isinstance(value, str) else value for key, value in given.items()}
        if values["mail_folder"] and (listed.get("mailbox") or "").lower() != (values["mailbox"] or "").lower():
            return strings["settings_folders_first"], gr.update()
        if values["mail_folder"]:
            values["mail_folder_name"] = listed.get("folders", {}).get(values["mail_folder"]) or (
                before.mail_folder_name if before.mail_folder == values["mail_folder"] else None)
        saved, refused, _ = write(lambda held: values, strings)      # a refused field by its label, never a value
        if saved is None:
            return refused, gr.update()
        if not trigger_on(saved) or connection_of(saved) is None:      # the trigger off, or another folder: a later connection
            try:                                                          # starts from its own moment (the runner does so too)
                forget_connection(saved.cases_root)
            except OSError:
                pass
        return strings["settings_buyer_saved"], _trigger_text(saved, strings)

    def settings_relabel(language_now: str, tenant_now: str, client_now: str, mailbox_now: str, send_via_now: str) -> tuple:
        strings = STRINGS[language_now]
        return (strings["settings_intro"], gr.update(label=strings["settings_tenant"]), gr.update(label=strings["settings_client"]),
                gr.update(value=strings["settings_sign_in"]), gr.update(value=strings["settings_sign_in_code"]),
                gr.update(value=strings["settings_sign_out"]), strings["settings_browser_note"],
                _account_text(known(tenant_now, client_now), strings), gr.update(label=strings["settings_mailbox"]),
                gr.update(label=strings["settings_folder"]), gr.update(value=strings["settings_folders"]),
                gr.update(value=strings["settings_test"]),
                gr.update(label=strings["settings_send_via"], choices=_via_choices(strings), value=send_via_now),
                gr.update(value=strings["settings_save"]), f"### {strings['settings_trigger']}", _trigger_text(current(), strings), "")

    _buyer_section(gr, app, language, settings_tab, read, write)
    _exact_section(gr, app, language, settings_tab, read, write, lambda: live.now().clock())
    with gr.Accordion(first["settings_connections"], open=False) as connections:
        intro = gr.Markdown(first["settings_intro"])
        with gr.Row():
            tenant = gr.Textbox(label=first["settings_tenant"])
            client = gr.Textbox(label=first["settings_client"])
        with gr.Row():
            sign_in_button = gr.Button(first["settings_sign_in"], variant="primary")
            code_button = gr.Button(first["settings_sign_in_code"])
            sign_out_button = gr.Button(first["settings_sign_out"])
        browser_note = gr.Markdown(first["settings_browser_note"])
        account_shown = gr.Markdown(first["settings_not_signed_in"])
        mailbox = gr.Textbox(label=first["settings_mailbox"])
        with gr.Row():
            folder = gr.Dropdown(choices=[], value=None, label=first["settings_folder"], scale=4)
            folders_button = gr.Button(first["settings_folders"], scale=1)
        test_button = gr.Button(first["settings_test"])
        send_via = gr.Radio(choices=_via_choices(first), value="auto", label=first["settings_send_via"])
        save_button = gr.Button(first["settings_save"], variant="primary")
        note = gr.Markdown("")
        trigger_title = gr.Markdown(f"### {first['settings_trigger']}")
        trigger_shown = gr.Markdown("")
        listed = gr.State({})                        # the folders listed and the mailbox they were listed for

        signed = [account_shown, folder, listed, note]
        app.load(settings_opened, inputs=language, outputs=[tenant, client, mailbox, folder, send_via, account_shown, trigger_shown, listed])
        sign_in_button.click(sign_in_browser, inputs=[tenant, client, mailbox, language], outputs=signed)
        code_button.click(sign_in_code, inputs=[tenant, client, mailbox, language], outputs=signed)
        sign_out_button.click(sign_out, inputs=[tenant, client, language], outputs=signed)
        folders_button.click(load_folders, inputs=[tenant, client, mailbox, language], outputs=[folder, listed, note])
        test_button.click(check_connection, inputs=[tenant, client, mailbox, folder, language], outputs=note)
        save_button.click(save_settings, inputs=[tenant, client, mailbox, folder, send_via, listed, language], outputs=[note, trigger_shown])
        language.change(settings_relabel, inputs=[language, tenant, client, mailbox, send_via],
                        outputs=[intro, tenant, client, sign_in_button, code_button, sign_out_button, browser_note, account_shown, mailbox,
                                 folder, folders_button, test_button, send_via, save_button, trigger_title, trigger_shown, note])
        _ai_section(gr, app, language, tab, tenant, client, mailbox, account_shown, begin, read, write)
        _extension_code(gr, app, language, current)
    language.change(lambda language_now: gr.update(label=STRINGS[language_now]["settings_connections"]), inputs=language,
                    outputs=connections)


def _extension_code(gr, app, language, current: Callable[[], Settings]) -> None:
    """The code of the browser extension, at the end of the connections: one line that cannot be edited, with a button to copy it,
    filled with capture_token of the settings file (current gives the settings in the file, or those in use) each time the page
    loads. _settings_tab builds it on the page of this PC only, with its settings file."""
    first = STRINGS["nl"]

    def extension_code_opened() -> str:
        return current().capture_token or ""

    def extension_code_relabel(language_now: str) -> dict:
        strings = STRINGS[language_now]
        return gr.update(label=strings["settings_extension_code"], info=strings["settings_extension_code_help"])

    code = gr.Textbox(label=first["settings_extension_code"], info=first["settings_extension_code_help"], interactive=False, max_lines=1,
                      buttons=["copy"])
    app.load(extension_code_opened, outputs=code)
    language.change(extension_code_relabel, inputs=language, outputs=code)


def _buyer_section(gr, app, language, settings_tab, read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable) -> None:
    """The buyer's part of the settings tab and its events, at its top (see the module docstring): the line of a settings file that
    cannot be read; the eleven settings of buyer_settings.BUYER_FIELDS in the groups of BUYER_GROUPS, each field with its label and
    its help, under one save button; then the suppliers, a table and a form for one supplier. read gives the settings in the file
    (or those in use, with the line), and write saves into it (both of _settings_tab). The fill (buyer_opened) runs when the page
    loads and each time settings_tab is selected. Two states of the page hold what the forms were filled with: the eleven texts,
    and the supplier form with the supplier it was filled for, so that a save writes what the buyer changed and nothing else."""
    first = STRINGS["nl"]

    def headers(strings: dict[str, str]) -> list[str]:
        return [strings[f"settings_supplier_column:{name}"] for name in ("name", "email", "lead_time", "review", "messages_off")]

    def table(settings: Settings, strings: dict[str, str]) -> dict:
        """The table of the suppliers: their values as text, a tick as the word for yes."""
        data = [[name, email, str(lead), strings["settings_tick"] if review else "", strings["settings_tick"] if off else ""]
                for name, email, lead, review, off in supplier_rows(settings)]
        return {"headers": headers(strings), "data": data}

    def choices(settings: Settings, strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings["settings_supplier_new"], NEW_SUPPLIER)] + [(name, name) for name in supplier_names(settings)]

    def dropdown(settings: Settings, strings: dict[str, str], choice: str | None) -> tuple[dict, str]:
        """The dropdown with the suppliers of the settings, on the choice when it is one of them and else on "Leverancier
        toevoegen": (its update, the value it then has)."""
        value = choice if choice != NEW_SUPPLIER and choice in supplier_names(settings) else NEW_SUPPLIER
        return gr.update(choices=choices(settings, strings), value=value), value

    def supplier_form(settings: Settings, choice: str) -> tuple:
        """The supplier form for a value of the dropdown: (the name, the address, the lead time, the two ticks, what the form is
        filled with). A listed supplier with its values as the settings hold them and its name fixed; "Leverancier toevoegen", or
        a name the settings do not hold: the empty form with the name open."""
        if choice == NEW_SUPPLIER or choice not in supplier_names(settings):
            return gr.update(value="", interactive=True), "", "", False, False, dict(NEW_FORM)
        parts = supplier_shown(settings, choice)
        return (gr.update(value=choice, interactive=False), parts["email"], parts["lead_time"], parts["review"], parts["off"],
                {"name": choice} | parts)

    def refused(problems: dict[str, str], strings: dict[str, str]) -> str:
        """The refused fields of a save, each by its label with its problem, never the value given."""
        return strings["settings_buyer_refused"].format(fields="; ".join(f"{strings[FIELD_LABELS[field]]}: {problem}"
                                                                          for field, problem in problems.items()))

    def top(line: str | None) -> str:
        """The line at the top of the tab: that of a settings file that cannot be read, in bold, or nothing."""
        return f"**{line}**" if line else ""

    def buyer_opened(language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the line of a file that cannot be read, the eleven settings of the file
        and what the form is filled with, the table of the suppliers, their dropdown on "Leverancier toevoegen" and the supplier
        form emptied. With a file that cannot be read, the settings in use."""
        strings = STRINGS[language_now]
        settings, line = read(strings)
        values = shown(settings, language_now)
        return (top(line), *[values[field] for field in BUYER_FIELDS], values, table(settings, strings),
                dropdown(settings, strings, NEW_SUPPLIER)[0], *supplier_form(settings, NEW_SUPPLIER))

    def save_buyer(price_pct, price_abs, lead_time_tolerance_days, first_cases_review, ask_wait_working_days, buyer_name, digest,
                   digest_time, buyer_email, language_setting, approver_email, filled: dict | None, language_now: str) -> tuple:
        """Write the fields whose text differs from the text the form was filled with, and no other: (a note, the line of a file
        that cannot be read, the eleven fields, what they are filled with). A refused field writes nothing and is named by its
        label, and the form keeps what was typed; nothing changed writes nothing; a file that cannot be read refuses with its
        line. After a save, and when nothing was changed, the fields show the values as the file now holds them. A form whose
        fill the page no longer knows (the page was started again while the browser tab stayed open) is filled again from the
        file, and nothing is written."""
        strings = STRINGS[language_now]
        same = [gr.update()] * (len(BUYER_FIELDS) + 1)       # the eleven fields and what they were filled with, left as they are
        settings, line = read(strings)
        if line is not None:
            return (line, top(line), *same)
        if not isinstance(filled, dict):
            values = shown(settings, language_now)
            return (strings["settings_buyer_refilled"], "", *[values[field] for field in BUYER_FIELDS], values)
        form = dict(zip(BUYER_FIELDS, (price_pct, price_abs, lead_time_tolerance_days, first_cases_review, ask_wait_working_days, buyer_name,
                                       digest, digest_time, buyer_email, language_setting, approver_email)))
        changed = changed_fields(form, filled)
        if not changed:                                      # nothing typed is lost: the form shows the file as it is now
            values = shown(settings, language_now)
            return (strings["settings_buyer_unchanged"], "", *[values[field] for field in BUYER_FIELDS], values)
        values, problems = parse(form, language_now, only=changed)
        if problems:
            return (refused(problems, strings), "", *same)
        saved, note, line = write(lambda held: values, strings)
        if saved is None:
            return (note, top(line), *same)
        values = shown(saved, language_now)
        return (strings["settings_buyer_saved"], "", *[values[field] for field in BUYER_FIELDS], values)

    def supplier_chosen(choice: str | None, language_now: str) -> tuple:
        """The dropdown changed, by the buyer or by a save: the supplier form with the supplier's values as the file holds them
        (its name fixed), or the empty form for "Leverancier toevoegen", and what the form is filled with."""
        return supplier_form(read(STRINGS[language_now])[0], choice or NEW_SUPPLIER)

    def supplier_picked() -> str:
        """The buyer's own choice in the dropdown: the note of the save before it is cleared."""
        return ""

    def save_supplier(choice: str | None, name: str, email: str, lead_time: str, review: bool, off: bool, filled: dict | None,
                      language_now: str) -> tuple:
        """Write the supplier of the form into the four supplier settings, built from the file as it is read inside the lock of the
        save: (a note, the line of a file that cannot be read, the table, the dropdown, the supplier form and what it is filled
        with). Of a listed supplier only the parts the buyer changed are written, its name as the file has it; with
        "Leverancier toevoegen" the typed name, trimmed, with every value typed. Nothing is written for a typed name that is
        listed already, for a new supplier with a name and nothing else, for a listed one with nothing changed, or for a form
        whose fill the page no longer knows (or that was filled for another supplier than the one chosen), which is filled
        again."""
        strings = STRINGS[language_now]
        same = [gr.update()] * 8                             # the table, the dropdown, the five of the form and what it was filled with
        settings, line = read(strings)
        if line is not None:
            return (line, top(line), *same)
        new = not choice or choice == NEW_SUPPLIER
        form = {"email": email, "lead_time": lead_time, "review": review, "off": off}
        if new:
            supplier, changes = (name or "").strip(), supplier_changes(form, NEW_FORM)
            if supplier == NEW_SUPPLIER:                     # the dropdown's own value for "Leverancier toevoegen" names no supplier
                return (refused({"supplier_name": strings["settings_problem:supplier_name"]}, strings), "", *same)
        elif not isinstance(filled, dict) or filled.get("name") != choice:
            return (strings["settings_buyer_refilled"], "", table(settings, strings), dropdown(settings, strings, choice)[0],
                    *supplier_form(settings, choice))
        else:
            supplier, changes = choice, supplier_changes(form, filled)
            if not changes:
                return (strings["settings_buyer_unchanged"], "", *same)
        read_inside = []                                     # the settings of the file as the save read them

        def build(held: Settings) -> dict:
            read_inside.append(held)
            if new and supplier in supplier_names(held):
                raise _Refused(strings["settings_supplier_listed"])
            values, problems = supplier_update(held, supplier, changes, language_now, chosen=not new)      # a chosen name as the file had it
            if problems:
                raise _Refused(refused(problems, strings))
            if new and not changes:
                raise _Refused(strings["settings_supplier_empty"])
            return values

        saved, note, line = write(build, strings)
        if saved is None and note == strings["settings_supplier_listed"]:      # the list as the file has it, to choose the supplier in
            return (note, "", table(read_inside[-1], strings), dropdown(read_inside[-1], strings, NEW_SUPPLIER)[0], *[gr.update()] * 6)
        if saved is None:
            return (note, top(line), *same)
        shown_choice, kept = dropdown(saved, strings, supplier)
        return (strings["settings_buyer_saved"], "", table(saved, strings), shown_choice, *supplier_form(saved, kept))

    def remove_supplier(choice: str | None, language_now: str) -> tuple:
        """Take the supplier chosen out of the four settings, its name as the file has it: (a note, the line of a file that cannot
        be read, the table, the dropdown on "Leverancier toevoegen", the supplier form emptied and what it is filled with)."""
        strings = STRINGS[language_now]
        same = [gr.update()] * 8
        if not choice or choice == NEW_SUPPLIER:
            return (strings["settings_supplier_choose"], gr.update(), *same)
        settings, line = read(strings)
        if line is not None:
            return (line, top(line), *same)
        gone = []                                            # the file no longer holds the supplier: nothing to take out

        def build(held: Settings) -> dict:
            if choice not in supplier_names(held):
                gone.append(choice)
                return {}
            return without_supplier(held, choice)

        saved, note, line = write(build, strings)
        if saved is None:
            return (note, top(line), *same)
        return (strings["settings_buyer_unchanged" if gone else "settings_buyer_saved"], "", table(saved, strings),
                dropdown(saved, strings, NEW_SUPPLIER)[0], *supplier_form(saved, NEW_SUPPLIER))

    def buyer_relabel(language_now: str, digest_now: str | None, language_setting_now: str | None, choice_now: str | None) -> tuple:
        """The page's language switched: the buyer's part in the other language, with what is typed and chosen kept. The note of
        the main save is cleared; the note of a supplier save stays until the buyer's own choice in the dropdown."""
        strings = STRINGS[language_now]
        settings, line = read(strings)
        fields = []
        for field in BUYER_FIELDS:
            update = {"label": strings[f"settings_field:{field}"], "info": strings[f"settings_help:{field}"]}
            if field == "digest":
                update |= {"choices": _digest_choices(strings), "value": digest_now}
            elif field == "language":
                update |= {"choices": _language_choices(strings), "value": language_setting_now}
            fields.append(gr.update(**update))
        relabelled = gr.update(label=strings["settings_supplier_choice"], choices=choices(settings, strings),
                               value=dropdown(settings, strings, choice_now)[1])
        return (top(line), *[f"### {strings[f'settings_group:{group}']}" for group, _ in BUYER_GROUPS], *fields, strings["settings_limit"],
                gr.update(value=strings["settings_buyer_save"]), "", f"### {strings['settings_suppliers']}",
                strings["settings_suppliers_help"], table(settings, strings), relabelled,
                gr.update(label=strings["settings_supplier_name"]), gr.update(label=strings["settings_supplier_email"]),
                gr.update(label=strings["settings_supplier_lead_time"]), gr.update(label=strings["settings_supplier_review"]),
                gr.update(label=strings["settings_supplier_messages_off"]), gr.update(value=strings["settings_supplier_save"]),
                gr.update(value=strings["settings_supplier_remove"]))

    file_line = gr.Markdown("", elem_id="settings-file-line")
    headings, fields = [], {}
    for group, names in BUYER_GROUPS:
        headings.append(gr.Markdown(f"### {first[f'settings_group:{group}']}"))
        for field in names:
            label, info = first[f"settings_field:{field}"], first[f"settings_help:{field}"]
            if field == "digest":
                fields[field] = gr.Radio(choices=_digest_choices(first), value=None, label=label, info=info)
            elif field == "language":
                fields[field] = gr.Radio(choices=_language_choices(first), value=None, label=label, info=info)
            else:
                fields[field] = gr.Textbox(label=label, info=info)
    limit = gr.Markdown(first["settings_limit"])
    save_button = gr.Button(first["settings_buyer_save"], variant="primary")
    note = gr.Markdown("", elem_id="settings-buyer-note")
    suppliers_title = gr.Markdown(f"### {first['settings_suppliers']}")
    suppliers_help = gr.Markdown(first["settings_suppliers_help"])
    suppliers = gr.Dataframe(value={"headers": headers(first), "data": []}, interactive=False, wrap=True)
    supplier = gr.Dropdown(choices=[(first["settings_supplier_new"], NEW_SUPPLIER)], value=NEW_SUPPLIER, label=first["settings_supplier_choice"])
    with gr.Row():
        name = gr.Textbox(label=first["settings_supplier_name"])
        email = gr.Textbox(label=first["settings_supplier_email"])
        lead_time = gr.Textbox(label=first["settings_supplier_lead_time"])
    with gr.Row():
        review = gr.Checkbox(value=False, label=first["settings_supplier_review"])
        off = gr.Checkbox(value=False, label=first["settings_supplier_messages_off"])
    with gr.Row():
        supplier_save = gr.Button(first["settings_supplier_save"])
        supplier_remove = gr.Button(first["settings_supplier_remove"])
    supplier_note = gr.Markdown("", elem_id="settings-supplier-note")
    filled = gr.State(None)                          # the eleven texts the form was filled with
    supplier_filled = gr.State(None)                 # the supplier form as it was filled, with the supplier it was filled for

    form = [fields[field] for field in BUYER_FIELDS]
    supplier_form_fields = [name, email, lead_time, review, off]
    supplier_shown_again = [supplier_note, file_line, suppliers, supplier, *supplier_form_fields, supplier_filled]
    gr.on(triggers=[app.load, settings_tab.select], fn=buyer_opened, inputs=language,
          outputs=[file_line, *form, filled, suppliers, supplier, *supplier_form_fields, supplier_filled])
    save_button.click(save_buyer, inputs=[*form, filled, language], outputs=[note, file_line, *form, filled])
    # The dropdown's change (the buyer's choice, or the value a save or a removal gave it) fills the form and leaves the note of a
    # save alone; the buyer's own choice (input) clears that note.
    supplier.change(supplier_chosen, inputs=[supplier, language], outputs=[*supplier_form_fields, supplier_filled])
    supplier.input(supplier_picked, outputs=supplier_note)
    supplier_save.click(save_supplier, inputs=[supplier, *supplier_form_fields, supplier_filled, language], outputs=supplier_shown_again)
    supplier_remove.click(remove_supplier, inputs=[supplier, language], outputs=supplier_shown_again)
    language.change(buyer_relabel, inputs=[language, fields["digest"], fields["language"], supplier],
                    outputs=[file_line, *headings, *form, limit, save_button, note, suppliers_title, suppliers_help, suppliers, supplier,
                             *supplier_form_fields, supplier_save, supplier_remove])


# The fields of the block "Koppeling met Exact" above the title row, in the order of the page: the six labels and the supplier's line.
_EXACT_FIELDS = ("po_label", "date_label", "supplier_label", "supplier_line", "version_label", "currency_label", "incoterm_label")


def _exact_section(gr, app, language, settings_tab, read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable,
                   clock: Callable[[], datetime]) -> None:
    """The block "Koppeling met Exact (bij de installatie)" of the settings tab and its events, folded away above the connections (see
    the module docstring; aid/review/exact_settings.py has what it shows and writes). read gives the settings in the file (or those in
    use, with the line of a file that cannot be read) and write saves into it (both of _settings_tab); clock is the time of the page's
    own actions, against which the state lines tell how long a file lies in the watched folder. Every control is made once, the SLOTS
    dropdowns of the title row among them, so that every handler is one of the page's functions. Two states of the page keep the title
    row found and what the form was filled with (saved_values: the two settings as the file held them at the load, at the select of
    the settings tab or after a save), by which a save from a form that another browser tab overtook is refused."""
    first = STRINGS["nl"]

    def state_text(settings: Settings, language_now: str) -> str:
        """The state lines, one paragraph each."""
        return "\n\n".join(state_lines(settings, PoStore(settings.po_store_root), clock(), language_now))

    def page_table(lines: list[list[str]], strings: dict[str, str]) -> dict:
        return {"headers": [strings["exact_page_line"], strings["exact_page_text"]], "data": lines}

    def first_page(sample: str | None, strings: dict[str, str]) -> tuple[dict, str]:
        """The first page of the sample as the table shows it, and the line under the table: that the table shows the first
        PAGE_LINES lines of a longer page, that the PDF has no text, or that it cannot be opened ("" for none)."""
        if not sample:
            return page_table([], strings), ""
        try:
            lines = page_lines(Path(sample), PAGE_LINES + 1)
        except SampleUnreadable:
            return page_table([], strings), strings["exact_problem:unopenable"]
        except Exception as error:                       # anything else, by the class of its error
            return page_table([], strings), strings["settings_failed"].format(code=type(error).__name__)
        if not lines:
            return page_table([], strings), strings["exact_problem:scan"]
        more = strings["exact_page_more"].format(n=PAGE_LINES) if len(lines) > PAGE_LINES else ""
        return page_table(lines[:PAGE_LINES], strings), more

    def header_table(rows: list[list[str]], strings: dict[str, str]) -> dict:
        return {"headers": [strings["exact_read_what"], strings["exact_read_value"]], "data": rows}

    def lines_table(rows: list[list[str]], language_now: str, layout=None) -> dict:
        """The table of order lines, with a width for each of its columns, so that its cells wrap inside the block (line_widths)."""
        return gr.update(value={"headers": line_headers(language_now, layout), "data": rows}, column_widths=line_widths(layout))

    def listed(texts: list[str]) -> str:
        return "\n".join(f"- {text}" for text in texts)

    def cleared(language_now: str) -> tuple:
        """The reading emptied: (the summary, the table of the header, the table of the lines, the problems and warnings)."""
        return "", header_table([], STRINGS[language_now]), lines_table([], language_now), ""

    def reading(tried: Trial | None, layout, language_now: str) -> tuple:
        """The reading of a trial as the block shows it, in the order of cleared: the summary with the number of order lines and
        whether the total can be worked out, only when the trial found no problem; the two tables; and a line for each problem and each
        warning of the reader."""
        if tried is None:
            return cleared(language_now)
        if tried.document is None:                   # a PDF without text: nothing read, and the problem that says so
            return (*cleared(language_now)[:3], listed(tried.problems))
        strings = STRINGS[language_now]
        header, lines = reading_shown(tried.document, language_now, layout)
        summary = "" if tried.problems else strings["exact_read"].format(n=len(tried.document.lines),
                                                                          answer=strings["exact_yes" if tried.total else "exact_no"])
        return summary, header_table(header, strings), lines_table(lines, language_now, layout), listed(tried.problems + tried.warnings)

    def meaning_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"exact_meaning:{meaning}"], meaning) for meaning in MEANINGS]

    def number_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
        return [(strings[f"exact_numbers:{value}"], value) for value in ("comma", "point")]

    def help_of(field: str, strings: dict[str, str]) -> str | None:
        """The help of a field above the title row: the PO label's own, the labels' shared one, none for the supplier's line."""
        return None if field == "supplier_line" else strings["exact_help:po_label" if field == "po_label" else "exact_help:label"]

    def slot_updates(words: list[str], meanings: list, strings: dict[str, str]) -> list:
        """The SLOTS dropdowns for a title row of at most SLOTS words: one for each word, labelled with it and on its meaning, the
        others hidden and empty."""
        meanings = [*meanings, *[None] * len(words)]
        return [gr.update(visible=True, label=word, choices=meaning_choices(strings), value=meanings[n]) for n, word in enumerate(words)] + [
            gr.update(visible=False, value=None) for _ in range(SLOTS - len(words))]

    def given(values: tuple) -> tuple:
        """The inputs of "Testen" and "Opslaan" after the sample: (the form, the title row, the meanings, the senders, what the form
        was filled with, the language)."""
        form = dict(zip(EXACT_FORM, values))
        meanings = list(values[len(EXACT_FORM) + 1:len(EXACT_FORM) + 1 + SLOTS])
        return form, list(values[len(EXACT_FORM)] or []), meanings, values[-3], values[-2], values[-1]

    def judged(sample: str | None, form: dict, title_row: list, meanings: list, senders: str, language_now: str) -> tuple:
        """The form, the sample and the senders judged: (the layout or None, the senders or None, the trial read of the sample with the
        layout or None, the problems before the reading: of the form and of a missing sample, the problems after it: of the senders).
        A sample that cannot be opened raises SampleUnreadable."""
        strings = STRINGS[language_now]
        layout, refused = layout_from_form(form | {"title_row": title_row}, meanings, language_now)
        listed_senders, sender_problem = senders_from(senders, language_now)
        before = [*refused.values(), *([] if sample else [strings["exact_problem:no_sample"]])]
        tried = trial(Path(sample), layout, language_now) if layout is not None and sample else None
        return layout, listed_senders, tried, before, [sender_problem] if sender_problem else []

    def exact_opened(language_now: str) -> tuple:
        """The page loaded, or the settings tab selected: the state lines, and the form filled with the layout and the senders of the
        settings file (the settings in use when the file cannot be read), a dropdown shown on its meaning for each word of the saved
        title row; what the form is filled with; and the line under the search of the title row. A saved title row of more than SLOTS
        words, which the form cannot show, leaves the title row and its dropdowns empty and says so; the layout stays in force."""
        strings = STRINGS[language_now]
        settings = read(strings)[0]
        form = form_from_layout(settings.po_layout)
        words, meanings, line = form["title_row"], form["columns"], ""
        if len(words) > SLOTS:
            words, meanings, line = [], [], strings["exact_title_saved_long"]
        return (state_text(settings, language_now), *[form[field] for field in EXACT_FORM], form["title_word"], words,
                *slot_updates(words, meanings, strings), ", ".join(settings.po_senders), saved_values(settings), line)

    def exact_sample_shown(sample: str | None, language_now: str) -> tuple:
        """A sample chosen, or taken away: (its first page line by line, the line under the table, the reading emptied, the note
        emptied). The reading of an earlier sample goes."""
        return (*first_page(sample, STRINGS[language_now]), *cleared(language_now), "")

    def exact_title_found(sample: str | None, word: str, title_row: list | None, *values) -> tuple:
        """"Kopregel zoeken": (the words of the first line of page 1 that holds the word, the SLOTS dropdowns, a note): a dropdown for
        each word, labelled with it, on the meaning chosen before when the title row is the one found before. No sample, a PDF that
        cannot be opened or has no text, no line with the word and a title row of more than SLOTS words leave the title row and the
        dropdowns as they are."""
        *meanings, language_now = values
        strings, title_row = STRINGS[language_now], list(title_row or [])
        same = (title_row, *[gr.update()] * SLOTS)
        if not sample:
            return (*same, strings["exact_problem:no_sample"])
        try:
            words = title_words(Path(sample), word)
            if words is None and not page_lines(Path(sample), limit=1):
                return (*same, strings["exact_problem:scan"])
        except SampleUnreadable:
            return (*same, strings["exact_problem:unopenable"])
        except Exception as error:
            return (*same, strings["settings_failed"].format(code=type(error).__name__))
        if words is None:
            return (*same, strings["exact_title_missing"])
        if len(words) > SLOTS:
            return (*same, strings["exact_title_long"])
        return (words, *slot_updates(words, meanings[:len(words)] if words == title_row else [], strings), strings["exact_title_found"])

    def exact_tested(sample: str | None, *values) -> tuple:
        """"Testen": the sample read with the layout of the form; nothing is written. (The summary, the two tables, the problems and
        warnings of the reading, and the note with the problems of the form, of a missing sample and of the senders, or that the sample
        cannot be opened.)"""
        form, title_row, meanings, senders, _, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            layout, _, tried, before, after = judged(sample, form, title_row, meanings, senders, language_now)
        except SampleUnreadable:
            return (*cleared(language_now), strings["exact_problem:unopenable"])
        except Exception as error:
            return (*cleared(language_now), strings["settings_failed"].format(code=type(error).__name__))
        return (*reading(tried, layout, language_now), " ".join(before + after))

    def exact_saved(sample: str | None, *values) -> tuple:
        """"Opslaan": the sample read as "Testen" reads it, and po_layout and po_senders written into the settings file when nothing
        is wrong (write, which writes the keys whose value changed): (the note, the state lines, what the form is now filled with, the
        reading). Any problem of the form, the sample or the senders refuses the save, each named in the page's language, and nothing
        is written; so does a settings file that cannot be read, with its line, and a file whose two settings are no longer those the
        form was filled with (another browser tab saved meanwhile), which the save sees inside the lock of update_settings_file."""
        form, title_row, meanings, senders, filled, language_now = given(values)
        strings = STRINGS[language_now]
        try:
            layout, listed_senders, tried, before, after = judged(sample, form, title_row, meanings, senders, language_now)
        except SampleUnreadable:
            return (strings["exact_not_saved"].format(problems=strings["exact_problem:unopenable"]), gr.update(), filled, *cleared(language_now))
        except Exception as error:
            return (strings["settings_failed"].format(code=type(error).__name__), gr.update(), filled, *cleared(language_now))
        shown_now = reading(tried, layout, language_now)
        problems = before + (tried.problems if tried is not None else []) + after
        if problems:
            return (strings["exact_not_saved"].format(problems=" ".join(problems)), gr.update(), filled, *shown_now)

        def build(held: Settings) -> dict:
            if not isinstance(filled, dict) or saved_values(held) != filled:
                raise _Refused(strings["exact_changed"])
            return {"po_layout": as_setting(layout), "po_senders": listed_senders}

        saved, note, _ = write(build, strings)
        if saved is None:
            return (note, gr.update(), filled, *shown_now)
        return (strings["exact_saved"], state_text(saved, language_now), saved_values(saved), *shown_now)

    def exact_state_shown(language_now: str) -> str:
        """The state lines as the settings file and the folders hold them now ("Stand vernieuwen")."""
        return state_text(read(STRINGS[language_now])[0], language_now)

    def exact_relabel(language_now: str, sample: str | None, numbers_now: str | None, title_row: list | None, *meanings) -> tuple:
        """The page's language switched: the block in the other language, with what is typed and chosen kept, the first page's table
        and the line under it included; the notes and the reading are emptied, for "Testen" to read the sample again in that language."""
        strings = STRINGS[language_now]
        table, line = first_page(sample, strings)
        return (gr.update(label=strings["exact_title"]), strings["exact_intro"], f"### {strings['exact_state_title']}",
                exact_state_shown(language_now), gr.update(value=strings["exact_refresh"]), f"### {strings['exact_part_po']}",
                gr.update(label=strings["exact_sample"]), gr.update(label=strings["exact_page"], value=table), line,
                *[gr.update(label=strings[f"exact_field:{field}"], info=help_of(field, strings)) for field in _EXACT_FIELDS],
                gr.update(label=strings["exact_field:title_word"]), gr.update(value=strings["exact_find"]), "",
                *[gr.update(choices=meaning_choices(strings), value=meaning) for meaning in meanings],
                gr.update(label=strings["exact_field:table_end"], info=strings["exact_help:table_end"]),
                gr.update(label=strings["exact_field:numbers"], choices=number_choices(strings), value=numbers_now),
                gr.update(label=strings["exact_field:per_piece"]),
                gr.update(label=strings["exact_field:senders"], info=strings["exact_help:senders"]), gr.update(value=strings["exact_test"]),
                gr.update(value=strings["exact_save"]), "", f"### {strings['exact_reading']}", *cleared(language_now))

    with gr.Accordion(first["exact_title"], open=False, elem_id="exact-block") as block:
        intro = gr.Markdown(first["exact_intro"])
        state_title = gr.Markdown(f"### {first['exact_state_title']}")
        state = gr.Markdown("", elem_id="exact-state")
        refresh = gr.Button(first["exact_refresh"])
        part = gr.Markdown(f"### {first['exact_part_po']}")
        sample = gr.File(label=first["exact_sample"], file_types=[".pdf"], type="filepath")
        page_shown = gr.Dataframe(value=page_table([], first), label=first["exact_page"], interactive=False, wrap=True, elem_id="exact-page")
        sample_note = gr.Markdown("", elem_id="exact-sample-note")
        fields = {}
        for names in (("po_label", "date_label"), ("supplier_label", "supplier_line"), ("version_label",), ("currency_label", "incoterm_label")):
            with gr.Row():
                for field in names:
                    fields[field] = gr.Textbox(label=first[f"exact_field:{field}"], info=help_of(field, first))
        with gr.Row():
            title_word = gr.Textbox(label=first["exact_field:title_word"], scale=4)
            find = gr.Button(first["exact_find"], scale=1)
        title_note = gr.Markdown("", elem_id="exact-title-note")
        slots = []
        for start in range(0, SLOTS, 4):
            with gr.Row():
                slots += [gr.Dropdown(choices=meaning_choices(first), value=None, label=str(n), visible=False) for n in range(start + 1, start + 5)]
        fields["table_end"] = gr.Textbox(label=first["exact_field:table_end"], info=first["exact_help:table_end"])
        fields["numbers"] = gr.Radio(choices=number_choices(first), value="comma", label=first["exact_field:numbers"])
        fields["per_piece"] = gr.Checkbox(value=False, label=first["exact_field:per_piece"])
        senders = gr.Textbox(label=first["exact_field:senders"], info=first["exact_help:senders"])
        with gr.Row():
            test_button = gr.Button(first["exact_test"])
            save_button = gr.Button(first["exact_save"], variant="primary")
        note = gr.Markdown("", elem_id="exact-note")
        reading_title = gr.Markdown(f"### {first['exact_reading']}")
        summary = gr.Markdown("", elem_id="exact-summary")
        header = gr.Dataframe(value=header_table([], first), interactive=False, wrap=True, elem_id="exact-header")
        lines = gr.Dataframe(value={"headers": line_headers("nl"), "data": []}, column_widths=line_widths(), interactive=False, wrap=True,
                             elem_id="exact-lines")
        problems = gr.Markdown("", elem_id="exact-problems")
        title_row = gr.State([])                     # the words of the title row found, whose dropdowns are shown
        filled = gr.State(None)                      # what the form was filled with: saved_values of the file at that moment

    form = [fields[field] for field in EXACT_FORM]
    read_out = [summary, header, lines, problems]
    gr.on(triggers=[app.load, settings_tab.select], fn=exact_opened, inputs=language,
          outputs=[state, *form, title_word, title_row, *slots, senders, filled, title_note])
    sample.change(exact_sample_shown, inputs=[sample, language], outputs=[page_shown, sample_note, *read_out, note])
    find.click(exact_title_found, inputs=[sample, title_word, title_row, *slots, language], outputs=[title_row, *slots, title_note])
    tried_with = [sample, *form, title_row, *slots, senders, filled, language]
    test_button.click(exact_tested, inputs=tried_with, outputs=[*read_out, note])
    save_button.click(exact_saved, inputs=tried_with, outputs=[note, state, filled, *read_out])
    refresh.click(exact_state_shown, inputs=language, outputs=state)
    language.change(exact_relabel, inputs=[language, sample, fields["numbers"], title_row, *slots],
                    outputs=[block, intro, state_title, state, refresh, part, sample, page_shown, sample_note,
                             *[fields[field] for field in _EXACT_FIELDS], title_word, find, title_note, *slots, fields["table_end"],
                             fields["numbers"], fields["per_piece"], senders, test_button, save_button, note, reading_title, *read_out])


def _ai_section(gr, app, language, tab: _Tab, tenant, client, mailbox, account_shown,
                begin: Callable[[str, str, dict[str, str]], tuple[MicrosoftAccount | None, str | None]],
                read: Callable[[dict[str, str]], tuple[Settings, str | None]], write: Callable) -> None:
    """The AI section of the settings tab and its events, after the mailbox in its connections (see the module docstring): tenant, client
    and mailbox are the fields of the tab, account_shown the line that names the signed-in account, begin gives the account for an
    action or the note that says why there is none, and read and write are those of _settings_tab: the settings in the file (or
    those in use, with the line of a file that cannot be read), and a save into it."""
    first = STRINGS["nl"]

    def ai_opened(language_now: str) -> str:
        """The tab opened: the reader of the settings file."""
        return read(STRINGS[language_now])[0].reader

    def sign_in_copilot(tenant_now: str, client_now: str, mailbox_now: str, language_now: str):
        """Sign in in the browser of this PC for the mailbox and for Copilot together, saying first that the page waits for it: (the
        account, a note)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant_now, client_now, strings)
        if refused:
            yield gr.update(), refused
            return
        yield gr.update(), strings["settings_waiting"]
        try:
            account.sign_in_browser(mail_scopes((mailbox_now or "").strip() or None) + COPILOT_SCOPES)
        except SignInFailed as error:
            yield _account_text(account, strings), strings["settings_sign_in_failed"].format(code=error)
            return
        except BrowserInteractionTimeoutError:       # nobody signed in within BROWSER_SECONDS, or no window opened
            yield gr.update(), strings["settings_browser_timeout"]
            return
        except Exception as error:
            yield gr.update(), strings["settings_failed"].format(code=type(error).__name__)
            return
        yield _account_text(account, strings), strings["settings_ai_signed_in"]

    def test_copilot(tenant_now: str, client_now: str, language_now: str) -> str:
        """One message to Copilot: the first 20 characters of its answer, or the error of the client (no token, no text of Microsoft)."""
        strings = STRINGS[language_now]
        account, refused = begin(tenant_now, client_now, strings)
        if refused:
            return refused
        try:
            answer = CopilotClient(account, session=tab.session).ask(COPILOT_TEST).text
        except (CopilotError, ReaderError, SignInNeeded) as error:
            return strings["settings_ai_failed"].format(error=error)
        except Exception as error:
            return strings["settings_failed"].format(code=type(error).__name__)
        return strings["settings_ai_answer"].format(answer=" ".join(answer.split())[:20])

    def save_reader(reader_now: str, language_now: str) -> tuple:
        """Write the reader chosen into the settings file, Copilot only once the tenant and the client are saved: (the reader the file
        then holds, a note). Nothing is written into a file that cannot be read: the note is then the line that says so."""
        strings = STRINGS[language_now]
        before, line = read(strings)
        if line is not None:
            return before.reader, line
        if reader_now == "llm" and not (before.ms_tenant_id and before.ms_client_id):
            return before.reader, strings["settings_ai_ids_needed"]
        saved, refused, _ = write(lambda held: {"reader": reader_now}, strings)
        if saved is None:
            return before.reader, refused
        return saved.reader, strings["settings_buyer_saved"]

    def ai_relabel(language_now: str, reader_now: str | None) -> tuple:
        strings = STRINGS[language_now]
        return (f"### {strings['settings_ai']}", strings["settings_ai_needs"], gr.update(value=strings["settings_ai_sign_in"]),
                gr.update(value=strings["settings_ai_test"]),
                gr.update(label=strings["settings_ai_reader"], choices=_reader_choices(strings), value=reader_now), "")

    title = gr.Markdown(f"### {first['settings_ai']}")
    needs = gr.Markdown(first["settings_ai_needs"])
    with gr.Row():
        copilot_button = gr.Button(first["settings_ai_sign_in"])
        copilot_test_button = gr.Button(first["settings_ai_test"])
    reader = gr.Radio(choices=_reader_choices(first), value=None, label=first["settings_ai_reader"])
    note = gr.Markdown("")

    app.load(ai_opened, inputs=language, outputs=reader)
    copilot_button.click(sign_in_copilot, inputs=[tenant, client, mailbox, language], outputs=[account_shown, note])
    copilot_test_button.click(test_copilot, inputs=[tenant, client, language], outputs=note)
    reader.input(save_reader, inputs=[reader, language], outputs=[reader, note])      # a choice of the user, not the value the tab loads
    language.change(ai_relabel, inputs=[language, reader], outputs=[title, needs, copilot_button, copilot_test_button, reader, note])


def _account(tenant: str, client: str) -> MicrosoftAccount:
    """The Microsoft account of the tenant and the client, with the token cache of the store in default_secrets_dir(), which the
    runner reads too."""
    return MicrosoftAccount(tenant, client, SecretStore(default_secrets_dir()))


def _account_text(account: MicrosoftAccount | None, strings: dict[str, str]) -> str:
    """The signed-in account in words, read from the token cache only: opening the tab asks Microsoft nothing, so that a network
    that does not answer cannot hold the tab. Whether the sign-in still gives a token shows when the connection is tested or the
    folders are loaded (_no_token_text)."""
    try:
        name = account.account() if account is not None else None
    except Exception:
        name = None
    return strings["settings_signed_in"].format(account=name) if name else strings["settings_not_signed_in"]


def _no_token_text(account: MicrosoftAccount, strings: dict[str, str]) -> str:
    """The note when no token was to be had (SignInNeeded): the sign-in of the account in the cache has lapsed, or none is signed
    in."""
    try:
        name = account.account()
    except Exception:
        name = None
    return strings["settings_lapsed"].format(account=name) if name else strings["settings_not_signed_in"]


def _trigger_text(settings: Settings, strings: dict[str, str]) -> str:
    """The state of the trigger for the settings, from mailbox_state.json (aid/sources/microsoft_mail.py)."""
    if not trigger_on(settings):
        return strings["settings_trigger_off"]
    state = connection_of(settings)
    if state is None:
        return strings["settings_trigger_pending"]
    if not state["last_sync"]:
        return strings["settings_trigger_unread"].format(since=_when(state["connected_at"]))
    return strings["settings_trigger_on"].format(since=_when(state["connected_at"]), last=_when(state["last_sync"]), saved=state["saved"])


def _via_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_via:{value}"], value) for value in SEND_VIA]


def _labels(fields: list[str], strings: dict[str, str]) -> str:
    """The refused fields by their labels on the tab (FIELD_LABELS), each once; a field the tab does not write by its name."""
    shown = [strings[FIELD_LABELS[name]] if (name := field.split(".")[0]) in FIELD_LABELS else field for field in fields]
    return ", ".join(dict.fromkeys(shown))


def _digest_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_digest:{value}"], value) for value in ("daily", "per_case")]


def _language_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_language:{value}"], value) for value in ("nl", "en")]


def _reader_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_reader:{value}"], value) for value in READERS]


def _when(moment: datetime | str) -> str:
    """A time of the mailbox as the tab shows it, in the local time of this PC: "2026-11-12 09:00"; a time without a zone is UTC."""
    moment = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    moment = moment.replace(tzinfo=timezone.utc) if moment.utcoffset() is None else moment
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
