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

The settings tab ("Instellingen") connects the work mailbox through the buyer's own Microsoft sign-in (aid/connections/): the
Directory (tenant) ID and the Application (client) ID of the app registration; "Aanmelden bij Microsoft", which opens Microsoft's
sign-in page in a browser window on the PC where the page runs, as the tab says, and says that it waits for it, and "Aanmelden met
een code", which shows a code and an address and then waits until the code has been entered; the signed-in account, read from the
token cache only, so that opening the tab asks Microsoft nothing, and "Afmelden"; the mailbox (empty: the account's own; another
mailbox signs in with the shared scopes); the folder, chosen from the mailbox's folders once signed in ("Mappen ophalen" lists them
again, for another mailbox, and a folder listed for another mailbox than the one in the field is not saved); "Verbinding testen",
which shows how many messages the folder holds and when the newest arrived, never a subject or a sender, or that the sign-in has
lapsed (as "Mappen ophalen" does); how the aid sends (send_via); and the state of the trigger, from mailbox_state.json
(aid/sources/microsoft_mail.py), its times in the local time of the PC. "Opslaan" validates the six settings and writes them into
the settings file with update_settings_file (aid/settings.py), and the runner takes them over at its next pass; a save that turns
the trigger off or names another folder removes the stored connection (forget_connection), so that a folder connected again later
is read from that moment. The tab never shows a token, and an error by its code ("invalid_grant"), its status ("HTTP 404"), its
class, a refused field by its label, a browser sign-in that timed out and a store the PC refuses by a fixed line, the latter
without its folder. On a page opened on a public share link the tab says only "Niet beschikbaar op een openbare link" and offers nothing,
and on a page built without its settings file (the Colab notebook) it offers nothing either.

The AI section of the settings tab is Microsoft 365 Copilot (aid/readers/copilot_client.py), under a short text of what Copilot
needs: "Aanmelden voor Copilot" signs in in the browser as "Aanmelden bij Microsoft" does, saying first that it waits, with the
permissions of the mailbox in the field (mail_scopes) and of Copilot together; "Copilot testen" sends Copilot one message, "Answer with the word OK only.", and shows "Copilot antwoordt" with
the first 20 characters of the answer, or the error of the client by its text, which names a status and Graph's error code
("Copilot refused: 403 Forbidden") and never a token or a message of Microsoft; and "Documenten lezen met", Copilot or "zonder AI
(parser)", writes the setting reader ("llm" or "parser") into the settings file as soon as it is chosen, as "Opslaan" writes the
six settings, Copilot only once the tenant and the client are saved.
"""
import asyncio
import io
import re
import warnings
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, get_args

from msal.oauth2cli.oauth2 import BrowserInteractionTimeoutError

from aid.case import Case, load_case
from aid.connections.microsoft import COPILOT_SCOPES, MicrosoftAccount, SignInFailed, SignInNeeded, mail_scopes
from aid.connections.secrets import SecretStore, default_secrets_dir
from aid.execution.ops import WriteOp
from aid.execution.writer import WriteLog, WriteLogEntry, WriterError
from aid.pipeline import NOT_SENT, Deps
from aid.readers.copilot_client import CopilotClient, CopilotError
from aid.readers.llm_client import ReaderError
from aid.review.app import (DECISIONS_FILE, CaseSummary, Decision, FlagRow, _decisions, _list_cases, _saved_result, decide,
                            load_case_view)
from aid.review.close import _records, close_case, undo
from aid.review.i18n import STRINGS, reason_label
from aid.settings import Settings, SettingsInvalid, is_client_id, is_tenant_id, load_settings, update_settings_file
from aid.sources.microsoft_mail import GraphError, connection_of, folder_summary, forget_connection, list_folders, trigger_on

_REVIEW_ITEM = re.compile(r"RI\d+")              # the ids of the review items (aid/pipeline.py)
SEND_VIA = get_args(Settings.model_fields["send_via"].annotation)     # the values of send_via, in the order the tab offers them
READERS = get_args(Settings.model_fields["reader"].annotation)        # the values of reader: "llm" (Copilot) and "parser"
COPILOT_TEST = "Answer with the word OK only."                         # the message of "Copilot testen"
# The label on the tab of each setting the tab writes, by which a refused field is named, never by its value.
FIELD_LABELS = {"ms_tenant_id": "settings_tenant", "ms_client_id": "settings_client", "mailbox": "settings_mailbox",
                "mail_folder": "settings_folder", "mail_folder_name": "settings_folder", "send_via": "settings_send_via",
                "reader": "settings_ai_reader"}
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


class _Tab(NamedTuple):
    """What the settings tab is built with (see build_app)."""
    file: Path | None                            # the settings file it writes; None: the tab offers nothing
    share: bool                                  # the page is opened on a public share link: the tab offers nothing
    account_factory: Callable[[str, str], MicrosoftAccount]
    session: object                              # the requests session that Graph is called through; None: requests


def build_app(deps: Deps, settings_file: Path | None = None, share: bool = False,
              account_factory: Callable[[str, str], MicrosoftAccount] | None = None, session=None) -> "gr.Blocks":  # noqa: F821
    """The review page, Dutch by default with a switch to English, in two tabs. On "Beoordelen": a dropdown of the cases offered
    (offered_cases), with the case and the write of the address's query selected when the page opens (selection_from_query); the
    supplier's document and the PO version as image galleries; the three lists as tables, with the four decisions on each review
    item and the tick for the article price on a price; the banner when a change sends the PO through approval again; the close and
    undo buttons. On "Instellingen": the settings of the work mailbox (see the module docstring), written into settings_file, and
    nothing but a line when the page is opened on a public link (share) or without a settings file. account_factory gives the
    Microsoft account of a tenant and a client (by default with the token cache of the store in default_secrets_dir(); the tests give
    a fake one), and session is the requests session Graph is called through. No rule code is shown, and no setting value outside
    the settings tab. Built inside an event loop of its own (see the module docstring). The settings tab ends with its AI section
    (see the module docstring), whose Copilot is called through the same session."""
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

    def choices(language: str) -> list[tuple[str, str]]:
        return [(_case_label(summary, STRINGS[language]), summary.case_id) for summary in offered_cases(cases_root)]

    def tables(lists: dict, language: str) -> list[dict]:
        headers = [STRINGS[language][column] for column in _COLUMNS]
        return [{"headers": headers, "data": page_rows(lists[name], language)} for name in ("flags", "matches", "uncompared")]

    def opened(language: str, request: gr.Request) -> tuple:
        """The page opened: the cases offered, with the case of the query selected, and the write of the query to select among the
        writes to undo once the case is shown (selection_from_query)."""
        params = getattr(request, "query_params", None) or {}
        case_id, entry_id = selection_from_query(cases_root, params)
        return gr.update(choices=choices(language), value=case_id), entry_id

    def show(case_id: str | None, language: str, wanted: str | None = None) -> tuple:
        """The case chosen: the banner, the documents, the lists, the writes to undo (with the write wanted selected when it is
        among them), the state the decisions are shown from, and a message ("" when there is none). A decisions file that cannot be
        read empties the view and says so."""
        nothing = (gr.update(visible=False), [], [], *tables(empty, language), gr.update(choices=[], value=None), None)
        if not case_id:
            return (*nothing, "")
        case = load_case(cases_root, case_id)
        try:
            view = load_case_view(case)
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
        text, still_open = close_text(load_case(cases_root, case_id), deps, language)
        return text, gr.update(choices=choices(language), value=case_id if still_open else None)

    def do_undo(case_id: str | None, entry_id: str | None, language: str) -> tuple:
        if not case_id or not entry_id:
            return STRINGS[language]["choose_write"], gr.update()
        case = load_case(cases_root, case_id)
        return undo_text(case, entry_id, deps, language), gr.update(choices=_undo_choices(case, STRINGS[language]), value=None)

    with gr.Blocks(analytics_enabled=False, title=first["title"]) as app:
        state = gr.State(None)
        wanted = gr.State(None)                      # the write of the query to undo, selected when its case is shown
        with gr.Row():
            title = gr.Markdown(f"# {first['title']}")
            language = gr.Radio(choices=[("Nederlands", "nl"), ("English", "en")], value="nl", label=first["language"])
        with gr.Tab(first["review_tab"]) as review_tab:
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
        with gr.Tab(first["settings_tab"]) as settings_tab:
            _settings_tab(gr, app, language, deps.settings, tab)

        view = [banner, doc_gallery, po_gallery, flags_table, matches_table, uncompared_table, undo_choice, state]
        app.load(opened, inputs=language, outputs=[case_choice, wanted])
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


# --- the settings tab ---

def _settings_tab(gr, app, language, started_with: Settings, tab: _Tab) -> None:
    """The content of the settings tab and its events, inside the tab (see the module docstring). started_with are the settings the
    page was started with, which the tab shows when its file cannot be read. The folders listed are kept as {"mailbox": the mailbox
    they were listed for, "folders": id -> display name}, so that a folder is never saved with another mailbox's address."""
    first = STRINGS["nl"]
    if tab.share or tab.file is None:
        key = "settings_share" if tab.share else "settings_no_file"
        notice = gr.Markdown(first[key])
        language.change(lambda language_now: STRINGS[language_now][key], inputs=language, outputs=notice)
        return
    accounts: dict[tuple[str, str], MicrosoftAccount] = {}

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
        """The settings in the file, or those the page was started with when the file cannot be read."""
        try:
            return load_settings(tab.file)
        except (OSError, ValueError):
            return started_with

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
        another mailbox than the one in the field is not saved."""
        strings, before, listed = STRINGS[language_now], current(), listed or {}
        given = {"ms_tenant_id": tenant, "ms_client_id": client, "mailbox": mailbox, "mail_folder": folder, "mail_folder_name": None,
                 "send_via": send_via}
        values = {key: (value.strip() or None) if isinstance(value, str) else value for key, value in given.items()}
        if values["mail_folder"] and (listed.get("mailbox") or "").lower() != (values["mailbox"] or "").lower():
            return strings["settings_folders_first"], gr.update()
        if values["mail_folder"]:
            values["mail_folder_name"] = listed.get("folders", {}).get(values["mail_folder"]) or (
                before.mail_folder_name if before.mail_folder == values["mail_folder"] else None)
        try:
            saved = update_settings_file(tab.file, values)
        except SettingsInvalid as error:                     # by the labels of the fields, never a value
            return strings["settings_not_saved"].format(fields=_labels(error.fields, strings)), gr.update()
        except (OSError, ValueError) as error:
            return strings["settings_failed"].format(code=type(error).__name__), gr.update()
        if not trigger_on(saved) or connection_of(saved) is None:      # the trigger off, or another folder: a later connection
            try:                                                          # starts from its own moment (the runner does so too)
                forget_connection(saved.cases_root)
            except OSError:
                pass
        return strings["settings_saved"], _trigger_text(saved, strings)

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
    _ai_section(gr, app, language, tab, tenant, client, mailbox, account_shown, begin, current)


def _ai_section(gr, app, language, tab: _Tab, tenant, client, mailbox, account_shown,
                begin: Callable[[str, str, dict[str, str]], tuple[MicrosoftAccount | None, str | None]],
                current: Callable[[], Settings]) -> None:
    """The AI section of the settings tab and its events, at its end (see the module docstring): tenant, client and mailbox are the
    fields of the tab, account_shown the line that names the signed-in account, begin gives the account for an action or the note
    that says why there is none, and current the settings in the file."""
    first = STRINGS["nl"]

    def ai_opened(language_now: str) -> str:
        """The tab opened: the reader of the settings file."""
        return current().reader

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
        then holds, a note)."""
        strings, before = STRINGS[language_now], current()
        if reader_now == "llm" and not (before.ms_tenant_id and before.ms_client_id):
            return before.reader, strings["settings_ai_ids_needed"]
        try:
            saved = update_settings_file(tab.file, {"reader": reader_now})
        except SettingsInvalid as error:                     # by the labels of the fields, never a value
            return before.reader, strings["settings_not_saved"].format(fields=_labels(error.fields, strings))
        except (OSError, ValueError) as error:
            return before.reader, strings["settings_failed"].format(code=type(error).__name__)
        return saved.reader, strings["settings_saved"]

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
    shown = [strings[FIELD_LABELS[field]] if field in FIELD_LABELS else field for field in fields]
    return ", ".join(dict.fromkeys(shown))


def _reader_choices(strings: dict[str, str]) -> list[tuple[str, str]]:
    return [(strings[f"settings_reader:{value}"], value) for value in READERS]


def _when(moment: datetime | str) -> str:
    """A time of the mailbox as the tab shows it, in the local time of this PC: "2026-11-12 09:00"; a time without a zone is UTC."""
    moment = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    moment = moment.replace(tzinfo=timezone.utc) if moment.utcoffset() is None else moment
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")
