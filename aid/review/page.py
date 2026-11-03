"""The review page itself, built with gradio (aid/review/app.py has the view it shows, aid/review/close.py the close it calls).

build_app builds the page and page_rows gives the rows of a list as the page shows them. gradio is imported inside build_app, so that
the rest of the review page works without it. gradio makes an asyncio event loop for each lock it creates outside a running loop
and leaves it open (safe_get_lock of gradio/utils.py, 6.31.0), which the garbage collector later reports as unclosed; build_app
therefore builds the page inside a short-lived event loop of its own, which it closes, and directly when a loop is running already
(a notebook). The page shows no rule code, no setting value and no error text: fields, reasons, writes and failures by their labels
in i18n.STRINGS.

The cases the page offers (offered_cases) are those in review and any case with a write to undo, whatever its status: a write made
in the system (status "done") that is not undone, so that a write the aid made on its own can be undone from the page. A change only prepared was not made in
the system and is not offered for undo, as the digest gives it no undo link. The page reads the query of its address when it opens
(selection_from_query): ?case=<case id>, the link of the digest, selects that case when the page offers it, and &undo=<entry id>,
the digest's undo link, also selects that write in the list of writes to undo. Opening a link undoes nothing: the buyer undoes the
write with the undo button. A case the page does not offer, or a write it does not offer for undo, selects nothing.
"""
import asyncio
import io
import re
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import get_args

from aid.case import Case, load_case
from aid.execution.ops import WriteOp
from aid.execution.writer import WriteLog, WriteLogEntry, WriterError
from aid.pipeline import NOT_SENT, Deps
from aid.review.app import (DECISIONS_FILE, CaseSummary, Decision, FlagRow, _decisions, _list_cases, _saved_result, decide,
                            load_case_view)
from aid.review.close import _records, close_case, undo
from aid.review.i18n import STRINGS, reason_label

_REVIEW_ITEM = re.compile(r"RI\d+")              # the ids of the review items (aid/pipeline.py)
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


def build_app(deps: Deps) -> "gr.Blocks":  # noqa: F821 (gradio is imported inside)
    """The review page, Dutch by default with a switch to English: a dropdown of the cases offered (offered_cases), with the case and
    the write of the address's query selected when the page opens (selection_from_query); the supplier's document and the PO
    version as image galleries; the three lists as tables, with the four decisions on each review item and the tick for the article
    price on a price; the banner when a change sends the PO through approval again; the close and undo buttons. No rule code and no
    setting value is shown. Built inside an event loop of its own (see the module docstring)."""
    gr = import_gradio()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_built(gr, deps))
        finally:
            loop.close()
    return _page(gr, deps)


async def _built(gr, deps: Deps):
    return _page(gr, deps)


def _page(gr, deps: Deps):
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
    return app
