"""Tests of the tab 'In Exact invoeren' of the review page (aid/review/page.py; its list in aid/review/hand_list.py, its strings in
aid/review/i18n.py), through its handlers by name as the page holds them: no browser. The cases are made-up cases run as the runner
runs them, their changes prepared in <cases_root>/prepared, where the page of the review command reads the list."""
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

from aid.case import load_case
from aid.execution.by_hand import MARKS_FILE, changes, mark, open_changes
from aid.execution.ops import WriteOp
from aid.execution.writer import PREPARED_FILE, PreparedWriter, WriteLog, WriteLogEntry, undo_case
from aid.notify import list_link
from aid.pipeline import record_dispositions, run_case
from aid.po_store import PoStore
from aid.review.hand_list import ids_of
from aid.review.i18n import STRINGS
from aid.review.page import _undo_choices, build_app, import_gradio
from aid.runner import build_deps
from aid.settings import load_settings
from aid.sources.upload import open_upload_case
from aid.tests.conftest import SETTINGS, deps

PO = {"MU08": "250108", "MU14": "250114"}             # MU08 prepares a price on PO line 1 and the article's price, MU14 a delivery date
SHOWN = ("tab", "intro", "rows", "block", "note", "seen")    # what each handler of the list gives, in the order of its outputs
NL, EN = STRINGS["nl"], STRINGS["en"]
LIST_HANDLERS = {"list_opened", "changes_entered", "changes_dropped", "block_entered", "hand_relabel", "done_opened", "change_reopened",
                 "change_put_back"}
ROWS = ["Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70",
        "Inkooporder 250114 (S14), regel 1 (KM-1401): leverdatum 16-11-2026 → week 48 (2026)",
        "artikel KM-0801: inkoopprijs onbekend → 12,70"]


def settings_file(tmp_path: Path, **values) -> Path:
    """A made-up settings file with relative folders, holding the tests' settings and the values given."""
    data = json.loads(SETTINGS.model_dump_json()) | {"cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                                                       "reader": "parser"} | values
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return path


def tab_page(tmp_path: Path, madeup_out: Path, cases=("MU08", "MU14"), share: bool = False, file: bool = True,
             limit: Decimal | None = None, **values) -> tuple:
    """The review page on a made-up settings file (none when file is False) with the values given, the made-up cases given run before
    as the runner runs them, with the prepared writer into <cases_root>/prepared as the review command builds it: (the page, that
    folder, the Deps). With a limit, the page's store compares the totals with it instead of the tests' made-up limit."""
    path = settings_file(tmp_path, **values)
    settings = load_settings(path)
    prepared = settings.cases_root / "prepared"
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(prepared)).model_copy(update={"settings": settings})
    for minute, name in enumerate(cases):
        case = open_upload_case(settings.cases_root, [madeup_out / f"{name}_conf.pdf"], PO[name], "confirmation", datetime(2026, 11, 12, 9, minute))
        record_dispositions(case, run_case(case, d))
    if limit is not None:
        d = d.model_copy(update={"store": PoStore(d.store.root, limit)})
    return build_app(d, settings_file=path if file else None, share=share), prepared, d


def handler(page, name: str):
    [found] = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]
    return found


def shown(page, name: str, *args) -> dict:
    """What a handler of the list gives, by name."""
    outputs = handler(page, name)(*args)
    assert len(outputs) == len(SHOWN)
    return dict(zip(SHOWN, outputs))


def value_of(listed: dict, words: str) -> str:
    """The value of the row of the list whose text holds the words."""
    [value] = [value for text, value in listed["rows"]["choices"] if words in text]
    return value


def hand_tab(page):
    gr = import_gradio()
    [tab] = [block for block in page.blocks.values() if isinstance(block, gr.Tab) and block.label == NL["hand_tab"]]
    return tab


def inside(block) -> list:
    return [block, *[found for child in getattr(block, "children", []) for found in inside(child)]]


def test_the_label_counts_the_open_changes(tmp_path, madeup_out):
    gr = import_gradio()
    page, prepared, d = tab_page(tmp_path, madeup_out)
    tab = hand_tab(page)                                      # as the page is built: the label without a number
    [refresh] = [block for block in inside(tab) if isinstance(block, gr.Button) and block.value == NL["refresh"]]
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "list_opened"]
    assert sorted(fill.targets) == sorted([(page._id, "load"), (tab._id, "select"), (refresh._id, "click")])     # read again when selected
    listed = shown(page, "list_opened", "nl")
    assert listed["tab"] == {"__type__": "update", "label": "In Exact invoeren (3)"} and shown(page, "list_opened", "en")["tab"]["label"] == "Enter in Exact (3)"
    assert [text for text, _ in listed["rows"]["choices"]] == ROWS and listed["rows"]["value"] == []
    assert (listed["intro"], listed["note"]) == (NL["hand_intro"], "")
    price, date, article = (value_of(listed, words) for words in ("prijs 12,50", "leverdatum", "inkoopprijs"))
    assert listed["seen"] == {"po:250108": [price], "po:250114": [date], "articles": [article]}      # the rows shown, per block
    for change in open_changes(prepared):
        mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    listed = shown(page, "list_opened", "nl")
    assert listed["tab"]["label"] == "In Exact invoeren" and listed["rows"]["choices"] == [] and listed["block"]["choices"] == []
    assert listed["intro"] == f"{NL['hand_intro']}\n\n{NL['hand_nothing']}" and NL["hand_nothing"] == "Er is niets in te voeren."
    assert shown(page, "list_opened", "en")["intro"].endswith("\n\nThere is nothing to enter.")


def test_ticked_rows_are_entered_or_dropped(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    root = d.settings.cases_root
    kept = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}       # the cases' files and the prepared rows
    listed = shown(page, "list_opened", "nl")
    price, date, article = (value_of(listed, words) for words in ("prijs 12,50", "leverdatum", "inkoopprijs"))
    entered = shown(page, "changes_entered", [price], "nl")
    assert entered["note"] == "1 wijziging genoteerd als ingevoerd in Exact." and entered["tab"]["label"] == "In Exact invoeren (2)"
    assert [value for _, value in entered["rows"]["choices"]] == [date, article] and entered["rows"]["value"] == []
    dropped = shown(page, "changes_dropped", [date, article], "nl")
    assert dropped["note"] == "2 wijzigingen vervallen: de assistent vraagt er niet meer om." and dropped["tab"]["label"] == "In Exact invoeren"
    marks = [json.loads(line) for line in (prepared / MARKS_FILE).read_text(encoding="utf-8").splitlines()]
    assert [(line["entry_id"], line["mark"], line["at"], line["by"]) for line in marks] == [
        (ids_of(price)[0], "entered", "2026-11-12T09:00:00", "buyer"), (ids_of(date)[0], "dropped", "2026-11-12T09:00:00", "buyer"),
        (ids_of(article)[0], "dropped", "2026-11-12T09:00:00", "buyer")]                  # at the time of the page's clock
    # Nothing of a case changed, nor the prepared rows: the marks are the one file written. The review tab's own undo list is as it
    # was: it offers writes made in a system, and these changes were only prepared.
    assert {path: path.read_bytes() for path in kept} == kept
    assert sorted(path for path in root.rglob("*") if path.is_file() and path not in kept) == [prepared / MARKS_FILE]
    assert {path.name for path in kept if path.parent == prepared} == {PREPARED_FILE}
    for case_id in sorted(path.name for path in root.iterdir() if (path / "case.json").exists()):
        assert _undo_choices(load_case(root, case_id), NL) == []
    # the same in English, the three put back on the list first
    for value in (price, date, article):
        mark(prepared, ids_of(value), "reopened", datetime(2026, 11, 12, 11, 0))
    assert shown(page, "changes_entered", [price, date], "en")["note"] == "2 changes noted as entered in Exact."
    assert shown(page, "changes_dropped", [article], "en")["note"] == "1 change is dropped: the assistant no longer asks for it."
    assert [change.state for change in open_changes(prepared)] == []


def test_a_whole_block_is_entered(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    [price] = [change for change in open_changes(prepared) if change.kind == "po_line_price"]
    # a second change of PO 250108, the quantity of line 2, prepared for the same case as a close of the review page prepares it
    PreparedWriter(prepared).apply(WriteOp(kind="po_line_quantity", po_number="250108", version=1, line_id="2", old_value="50", new_value="60"),
                                   price.case_ids[0], "B01", {}, datetime(2026, 11, 12, 9, 30))
    listed = shown(page, "list_opened", "nl")
    assert listed["block"]["choices"] == [("Inkooporder 250108 (S08)", "po:250108"), ("Inkooporder 250114 (S14)", "po:250114"),
                                          ("Artikelprijzen", "articles")]
    assert (listed["block"]["value"], listed["block"]["label"]) == (None, "Blok") and listed["tab"]["label"] == "In Exact invoeren (4)"
    done = shown(page, "block_entered", "po:250108", listed["seen"], "nl")
    assert done["note"] == "2 wijzigingen genoteerd als ingevoerd in Exact." and done["tab"]["label"] == "In Exact invoeren (2)"
    assert [choice for choice, _ in done["block"]["choices"]] == ["Inkooporder 250114 (S14)", "Artikelprijzen"]
    assert [change.kind for change in open_changes(prepared)] == ["article_price", "po_line_date"]   # the article's price is a block of its own
    assert shown(page, "block_entered", "articles", done["seen"], "en")["note"] == "1 change noted as entered in Exact."
    gone = shown(page, "block_entered", "po:250108", listed["seen"], "nl")                          # pressed again in a tab not read again
    assert gone["note"] == NL["hand_nothing_noted"] and gone["tab"]["label"] == "In Exact invoeren (1)"


def test_nothing_ticked_writes_nothing(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    for language, nothing in (("nl", []), ("en", None)):
        strings = STRINGS[language]
        for name in ("changes_entered", "changes_dropped"):
            result = shown(page, name, nothing, language)
            assert result["note"] == strings["hand_tick_first"] and result["tab"]["label"] == strings["hand_tab_count"].format(n=3)
        assert shown(page, "block_entered", None if language == "nl" else "", {}, language)["note"] == strings["hand_tick_first"]
    assert (NL["hand_tick_first"], EN["hand_tick_first"]) == ("Vink eerst aan wat u bedoelt.", "Tick what you mean first.")
    assert not (prepared / MARKS_FILE).exists() and len(open_changes(prepared)) == 3


def test_the_query_selects_the_tab(tmp_path, madeup_out):
    gr = import_gradio()
    page, prepared, d = tab_page(tmp_path, madeup_out)
    [tabs] = [block for block in page.blocks.values() if isinstance(block, gr.Tabs)]
    assert [(tab.label, tab.id) for tab in tabs.children] == [(NL["review_tab"], "beoordelen"), (NL["hand_tab"], "invoeren"),
                                                              (NL["settings_tab"], "instellingen")]
    [load] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "opened"]
    assert load.targets == [(page._id, "load")] and load.outputs[-1] is tabs
    link = list_link(d.settings)                              # the digest's link, read as the page reads the query of its address
    assert link == "http://localhost:7860/?tab=invoeren"
    case, wanted, selected = load.fn("nl", SimpleNamespace(query_params=dict(parse_qsl(urlsplit(link).query))))
    assert selected == {"__type__": "update", "selected": "invoeren"} and (case["value"], wanted) == (None, None)
    for query in ({}, {"tab": "instellingen"}, {"tab": "Invoeren"}, {"case": "250108_20261112T090000"}):
        assert load.fn("nl", SimpleNamespace(query_params=query))[2] == {"__type__": "update"}, query


def test_the_tab_offers_nothing_on_a_share_link(tmp_path, madeup_out):
    for share, file, key in ((True, True, "settings_share"), (False, False, "settings_no_file")):
        page, prepared, d = tab_page(tmp_path / key, madeup_out, share=share, file=file)
        tab = hand_tab(page)
        assert [type(child).__name__ for child in tab.children] == ["Markdown"] and tab.children[0].value == NL[key]
        assert not {getattr(f.fn, "__name__", "") for f in page.fns.values()} & LIST_HANDLERS
        [relabel] = [f.fn for f in page.fns.values() if tab in f.outputs]
        assert relabel("en") == (EN[key], {"__type__": "update", "label": "Enter in Exact"}), key
    assert NL["settings_share"] == "Niet beschikbaar op een openbare link"
    own = tab_page(tmp_path / "own", madeup_out)[0]                                         # the page of this PC has them all
    assert LIST_HANDLERS <= {getattr(f.fn, "__name__", "") for f in own.fns.values()}


# --- what the brief's tests above leave to the tab: the lines under the intro, the language, the controls and the texts ---

def test_the_tab_says_how_many_lines_could_not_be_read(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    with open(prepared / PREPARED_FILE, "ab") as f:
        f.write(b"9f0e1d2c,250108_20261112T090000,250108,1,2,,po_line_quan")             # the last line, cut while it is written
    listed = shown(page, "list_opened", "nl")
    assert listed["intro"] == f"{NL['hand_intro']}\n\n1 regel van de lijst kon niet worden gelezen; vernieuw de lijst over een minuut."
    assert listed["tab"]["label"] == "In Exact invoeren (3)"                                # the lines around it are read
    (prepared / MARKS_FILE).write_text('{"entry_id": "9f0e", "mark": "ent', encoding="utf-8")
    assert shown(page, "list_opened", "en")["intro"].endswith("\n\n2 lines of the list could not be read; refresh the list in a minute.")
    assert shown(page, "list_opened", "nl")["intro"].endswith("\n\n2 regels van de lijst konden niet worden gelezen; vernieuw de lijst over een minuut.")


def test_a_file_of_the_list_that_cannot_be_opened_leaves_the_tab_as_it_was_with_a_note(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    listed = shown(page, "list_opened", "nl")
    price = value_of(listed, "prijs 12,50")
    (prepared / MARKS_FILE).mkdir()                                  # a folder where the marks belong: the file cannot be opened
    same = {"__type__": "update"}
    for name, args in (("list_opened", ("nl",)), ("changes_entered", ([price], "nl")), ("changes_dropped", ([price], "nl")),
                       ("block_entered", ("po:250108", listed["seen"], "nl"))):
        result = shown(page, name, *args)
        assert result["note"] in [NL["settings_failed"].format(code=code) for code in ("IsADirectoryError", "PermissionError")], name
        assert result["tab"] == {"__type__": "update", "label": "In Exact invoeren (?)"}, name          # not a list with nothing to enter
        assert [result[part] for part in ("intro", "rows", "block", "seen")] == [same] * 4, name
    relabelled = handler(page, "hand_relabel")("en", [price], None)
    assert relabelled[0] == {"__type__": "update", "label": "Enter in Exact (?)"} and relabelled[4].startswith("This did not work (")
    assert relabelled[6] == {"__type__": "update", "value": "Refresh the list"}
    assert (NL["hand_tab_unread"], EN["hand_tab_unread"]) == ("In Exact invoeren (?)", "Enter in Exact (?)")


def test_an_order_above_the_authorization_shows_the_hint_and_no_amount(tmp_path, madeup_out):
    gr = import_gradio()
    page, prepared, d = tab_page(tmp_path, madeup_out, limit=Decimal("100.13"))         # PO 250114 (272.40) above it, PO 250108 (58.50) not
    listed = shown(page, "list_opened", "nl")
    assert listed["intro"] == f"{NL['hand_intro']}\n\n**Inkooporder 250114 (S14)** {NL['hand_hint']}"
    assert [text for text, _ in listed["rows"]["choices"]] == ROWS and [label for label, _ in listed["block"]["choices"]][1] == "Inkooporder 250114 (S14)"
    english = shown(page, "list_opened", "en")["intro"]
    assert english.endswith("**Purchase order 250114 (S14)** Note: after this change the purchase order must be approved again.")
    seen = json.dumps([listed, english], default=str)
    assert not [amount for amount in ("100.13", "100,13", "272.4", "272,4", "58.5", "58,5") if amount in seen]
    config = json.dumps(page.config, default=str)                                          # the page as the browser gets it
    assert "100.13" not in config and "100,13" not in config
    assert [block for block in inside(hand_tab(page)) if isinstance(block, gr.Markdown) and "100" in str(block.value)] == []


def test_switching_the_language_relabels_the_tab_and_keeps_what_is_ticked(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    price = value_of(shown(page, "list_opened", "nl"), "prijs 12,50")
    (tab, intro, rows, block, note, seen, refresh, entered_above, dropped_above, entered, dropped,
     whole) = handler(page, "hand_relabel")("en", [price, "gone"], "po:250114")
    assert tab == {"__type__": "update", "label": "Enter in Exact (3)"} and (intro, note) == (EN["hand_intro"], "")
    assert [text for text, _ in rows["choices"]][0] == "Purchase order 250108 (S08), line 1 (KM-0801): price 12.50 → 12.70"
    assert rows["value"] == [price] and (block["value"], block["label"]) == ("po:250114", "Block")         # a row no longer listed is let go
    assert seen["po:250108"] == [price]
    assert [update["value"] for update in (refresh, entered_above, dropped_above, entered, dropped, whole)] == [
        "Refresh the list", "The ticked rows have been entered", "Do not enter the ticked rows", "The ticked rows have been entered",
        "Do not enter the ticked rows", "Everything of this block has been entered"]
    assert handler(page, "hand_relabel")("nl", [], "po:999")[3]["value"] is None


def test_the_tab_is_wired_as_the_page_shows_it(tmp_path, madeup_out):
    """The tab's fixed controls in their order, and the trigger, the inputs and the outputs of each of its handlers: every press reads
    the list again and shows it. Each control exists when the page is built, none inside a rendered function, and the buttons stand
    outside the rows."""
    gr = import_gradio()
    page, prepared, d = tab_page(tmp_path, madeup_out)
    tab = hand_tab(page)
    language = [block for block in page.blocks.values() if isinstance(block, gr.Radio) and block.label == NL["language"]][0]

    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "list_opened"]
    state = fill.outputs[-1]

    def name(component) -> str:
        if component is tab:
            return "tab"
        if component is language:
            return "language"
        if component is state:
            return "seen"
        if isinstance(component, gr.Button):
            return f"button {component.value}"
        return getattr(component, "label", None) or getattr(component, "elem_id", None) or type(component).__name__

    def bound(fn_name: str) -> tuple:
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == fn_name]
        return (sorted((name(page.blocks[block]) if block != page._id else "page", event) for block, event in f.targets),
                [name(component) for component in f.inputs], [name(component) for component in f.outputs])

    order = [name(block) for block in inside(tab) if isinstance(block, (gr.Markdown, gr.Button, gr.CheckboxGroup, gr.Dropdown))]
    entered, dropped, whole, refresh = (f"button {NL[key]}" for key in ("hand_entered", "hand_dropped", "hand_block_entered", "refresh"))
    assert order[:10] == ["hand-intro", refresh, entered, dropped, "hand-rows", entered, dropped, "Blok", whole, "hand-note"]   # the two above and below
    assert order[10:] == ["hand-done", "Afgehandelde wijziging", f"button {NL['hand_reopen']}", f"button {NL['hand_put_back']}",
                          "hand-done-note", "hand-confirmations"]                      # the two sections of Task 35 under the list
    listed = ["tab", "hand-intro", "hand-rows", "Blok", "hand-note", "seen"]
    assert bound("list_opened") == (sorted([("page", "load"), ("tab", "select"), (refresh, "click")]), ["language"], listed)
    assert bound("changes_entered") == ([(entered, "click")] * 2, ["hand-rows", "language"], listed)
    assert bound("changes_dropped") == ([(dropped, "click")] * 2, ["hand-rows", "language"], listed)
    assert bound("block_entered") == ([(whole, "click")], ["Blok", "seen", "language"], listed)
    assert bound("hand_relabel") == ([("language", "change")], ["language", "hand-rows", "Blok"],
                                     [*listed, refresh, entered, dropped, entered, dropped, whole])
    [rows] = [block for block in inside(tab) if isinstance(block, gr.CheckboxGroup)]
    assert rows.show_label is False and rows.choices == [] and isinstance(tab.parent, gr.Tabs) and rows.elem_classes == ["hand-rows"]
    # one tick box under the other: a style rule given to the page through the head of an HTML block of the tab, for the group's class
    [style] = [block for block in inside(tab) if isinstance(block, gr.HTML)]
    assert ".hand-rows .wrap" in style.head and "flex-direction: column" in style.head and style.head.startswith("<style>")


def test_the_texts_of_the_tab_in_both_languages():
    hand = {key for key in NL if key.startswith("hand_")}
    assert hand == {key for key in EN if key.startswith("hand_")}
    brief = {"hand_tab": ("In Exact invoeren", "Enter in Exact"), "hand_tab_count": ("In Exact invoeren ({n})", "Enter in Exact ({n})"),
             "hand_intro": ("De assistent verandert zelf niets in Exact. Hieronder staat wat u in Exact invoert. Vink aan wat u hebt ingevoerd.",
                            "The assistant changes nothing in Exact itself. Below is what you enter in Exact. Tick what you have entered."),
             "hand_nothing": ("Er is niets in te voeren.", "There is nothing to enter."),
             "hand_block_po": ("Inkooporder {number} ({supplier})", "Purchase order {number} ({supplier})"),
             "hand_block_articles": ("Artikelprijzen", "Article prices"),
             "hand_row:po_line_price": ("regel {line} ({item}): prijs {old} → {new}", "line {line} ({item}): price {old} → {new}"),
             "hand_row:po_line_date": ("regel {line} ({item}): leverdatum {old} → {new}", "line {line} ({item}): delivery date {old} → {new}"),
             "hand_row:po_line_quantity": ("regel {line} ({item}): aantal {old} → {new}", "line {line} ({item}): quantity {old} → {new}"),
             "hand_row:article_price": ("artikel {item}: inkoopprijs {old} → {new}", "article {item}: purchase price {old} → {new}"),
             "hand_unknown": ("onbekend", "unknown"),
             "hand_hint": ("Let op: na deze wijziging moet de inkooporder opnieuw gefiatteerd worden.",
                           "Note: after this change the purchase order must be approved again."),
             "hand_entered": ("Aangevinkte regels zijn ingevoerd", "The ticked rows have been entered"),
             "hand_dropped": ("Aangevinkte regels niet invoeren", "Do not enter the ticked rows"),
             "hand_block_entered": ("Alles van dit blok is ingevoerd", "Everything of this block has been entered"),
             "hand_block": ("Blok", "Block"),
             "hand_tick_first": ("Vink eerst aan wat u bedoelt.", "Tick what you mean first."),
             "hand_entered_one": ("1 wijziging genoteerd als ingevoerd in Exact.", "1 change noted as entered in Exact."),
             "hand_entered_many": ("{n} wijzigingen genoteerd als ingevoerd in Exact.", "{n} changes noted as entered in Exact."),
             "hand_dropped_one": ("1 wijziging vervalt: de assistent vraagt er niet meer om.", "1 change is dropped: the assistant no longer asks for it."),
             "hand_dropped_many": ("{n} wijzigingen vervallen: de assistent vraagt er niet meer om.",
                                   "{n} changes are dropped: the assistant no longer asks for them."),
             "hand_unreadable_many": ("{n} regels van de lijst konden niet worden gelezen; vernieuw de lijst over een minuut.",
                                      "{n} lines of the list could not be read; refresh the list in a minute."),
             # the review round: Z2, Z4 and Z9
             "hand_row:article_price_back": ("artikel {item}: inkoopprijs {old} terugzetten naar de prijs van daarvoor",
                                             "article {item}: purchase price {old} back to the price before it"),
             "hand_tab_unread": ("In Exact invoeren (?)", "Enter in Exact (?)"),
             "hand_nothing_noted": ("Er is niets genoteerd: de aangevinkte regels staan niet meer open (al afgehandeld, of vervangen door een "
                                    "nieuwere waarde). De lijst is bijgewerkt.",
                                    "Nothing was noted: the ticked rows are no longer open (already handled, or replaced by a newer value). "
                                    "The list has been updated.")}
    assert {key: (NL[key], EN[key]) for key in brief} == brief
    assert (NL["refresh"], EN["refresh"]) == ("Lijst vernieuwen", "Refresh the list")         # the button of the review tab's own list
    for strings in (NL, EN):
        texts = [strings[key] for key in hand]
        assert not [text for text in texts if re.search(r"\bR\d{2}\b|—|–", text)]               # no rule code, no dash as punctuation


# --- the review round: the block press, the undo through the manual's recipe, the press that notes nothing ---

def test_the_block_button_marks_only_the_rows_the_page_showed(tmp_path, madeup_out):
    """A change prepared after the list was read stays open when the buyer presses "Alles van dit blok is ingevoerd" (Z1): another
    line of the order, and a newer value of the line he entered, which replaces the value he saw; the note counts what was marked."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    listed = shown(page, "list_opened", "nl")                                    # what the buyer sees: one row of PO 250108
    [price] = [change for change in open_changes(prepared) if change.kind == "po_line_price"]
    writer = PreparedWriter(prepared)
    newer_line = writer.apply(WriteOp(kind="po_line_date", po_number="250108", version=1, line_id="1", old_value="2026-11-16",
                                      new_value="2026-11-30"), price.case_ids[0], "R02", {}, datetime(2026, 11, 12, 9, 30)).entry_id
    done = shown(page, "block_entered", "po:250108", listed["seen"], "nl")
    assert done["note"] == "1 wijziging genoteerd als ingevoerd in Exact." and done["tab"]["label"] == "In Exact invoeren (3)"
    states = {change.ids[0]: change.state for change in changes(prepared)[0]}
    assert states[price.ids[0]] == "entered" and states[newer_line] == "open"   # the date he never saw stays on the list
    assert "Inkooporder 250108 (S08), regel 1 (KM-0801): leverdatum 16-11-2026 → 30-11-2026" in [text for text, _ in done["rows"]["choices"]]
    # a newer document asks 12.90 for the line while the buyer enters 12.70 from the list he read
    other, other_prepared, _ = tab_page(tmp_path / "other", madeup_out)
    seen = shown(other, "list_opened", "nl")["seen"]
    [price] = [change for change in open_changes(other_prepared) if change.kind == "po_line_price"]
    newer = PreparedWriter(other_prepared).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50",
                                                         new_value="12.90"), price.case_ids[0], "R04", {}, datetime(2026, 11, 12, 9, 30)).entry_id
    pressed = shown(other, "block_entered", "po:250108", seen, "nl")
    assert pressed["note"] == NL["hand_nothing_noted"]                          # 12.70 was replaced: nothing is noted, and 12.90 is asked
    states = {change.ids[0]: change.state for change in changes(other_prepared)[0]}
    assert states[price.ids[0]] == "replaced" and states[newer] == "open" and not (other_prepared / MARKS_FILE).exists()
    assert "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,90" in [text for text, _ in pressed["rows"]["choices"]]


def test_an_undo_through_the_manuals_recipe_cancels_a_change_not_entered_and_asks_back_one_entered(tmp_path, madeup_out):
    """MANUAL 3.10's recipe, undo_case through the runner's writer (Z2): with nothing entered, MU08's two changes leave the list and
    nothing is asked for its order; with both entered, the list asks to put the price back and the article's price from before."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    runner = build_deps(d.settings, {})                                          # the recipe of the manual, as it stands there
    assert runner.writer.path.parent == prepared
    undo_case(d.settings.cases_root / case_id, runner.writer, datetime(2026, 11, 12, 11, 0))
    listed = shown(page, "list_opened", "nl")
    assert listed["tab"]["label"] == "In Exact invoeren (1)" and [text for text, _ in listed["rows"]["choices"]] == ROWS[1:2]
    entered, entered_prepared, entered_deps = tab_page(tmp_path / "entered", madeup_out)
    for change in open_changes(entered_prepared):
        if change.po_number == "250108":
            mark(entered_prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    [case_id] = [path.name for path in entered_deps.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    undo_case(entered_deps.settings.cases_root / case_id, build_deps(entered_deps.settings, {}).writer, datetime(2026, 11, 12, 11, 0))
    for language, rows in (("nl", ["Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,70 → 12,50", ROWS[1],
                                   "artikel KM-0801: inkoopprijs 12,70 terugzetten naar de prijs van daarvoor"]),
                           ("en", ["Purchase order 250108 (S08), line 1 (KM-0801): price 12.70 → 12.50",
                                   "Purchase order 250114 (S14), line 1 (KM-1401): delivery date 16-11-2026 → week 48 (2026)",
                                   "article KM-0801: purchase price 12.70 back to the price before it"])):
        listed = shown(entered, "list_opened", language)
        assert [text for text, _ in listed["rows"]["choices"]] == rows, language
        assert listed["tab"]["label"] == STRINGS[language]["hand_tab_count"].format(n=3)


def test_a_press_that_notes_nothing_says_so(tmp_path, madeup_out):
    """Two browser tabs read the list; the first ticks the price as entered, the second, not read again, drops the same row: nothing
    is noted, and the note says why (Z9). One change noted keeps its own note."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    first, second = shown(page, "list_opened", "nl"), shown(page, "list_opened", "nl")
    assert shown(page, "changes_entered", [value_of(first, "prijs 12,50")], "nl")["note"] == "1 wijziging genoteerd als ingevoerd in Exact."
    late = shown(page, "changes_dropped", [value_of(second, "prijs 12,50")], "nl")
    assert late["note"] == NL["hand_nothing_noted"] and late["tab"]["label"] == "In Exact invoeren (2)"
    assert shown(page, "changes_entered", [value_of(second, "prijs 12,50")], "en")["note"] == EN["hand_nothing_noted"]
    assert [json.loads(line)["mark"] for line in (prepared / MARKS_FILE).read_text(encoding="utf-8").splitlines()] == ["entered"]


def manual_sections() -> dict[str, str]:
    """The manual's sections by their number ("2.6", "3.10", "7"), each with its own text only."""
    sections, number = {}, None
    for line in Path("aid/MANUAL.md").read_text(encoding="utf-8").splitlines():
        if (heading := re.match(r"#{2,3} (\d+(?:\.\d+)?)\.? ", line)) is not None:
            number = heading[1]
        if number is not None:
            sections[number] = sections.get(number, "") + line + "\n"
    return sections


def test_the_documents_say_what_the_tab_does():
    """The manual's section 2.6 quotes the tab word for word, 2.1 and the Dutch guide the digest's line, 3.10 says why the aid writes
    nothing in Exact and what a program door would need; no sentence says any more that the researcher applies the prepared changes
    by hand, and the README names no one where it says what the aid needs."""
    manual, whole = manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    assert manual["2.6"].startswith("### 2.6 The tab In Exact invoeren\n") and manual["2.7"].startswith("### 2.7 Your settings on the page\n")
    for key in ("hand_intro", "hand_entered", "hand_dropped", "hand_block", "hand_block_entered", "hand_tick_first", "hand_hint", "hand_nothing",
                "hand_entered_one", "hand_dropped_one", "hand_unreadable_one", "refresh"):
        assert f"\"{NL[key]}\"" in manual["2.6"], key                                    # quoted word for word
    for row in ROWS:
        assert row in manual["2.6"] or row.split(", ", 1)[-1] in manual["2.6"], row
    assert "In Exact in te voeren: 2 wijzigingen. Open het tabblad In Exact invoeren: <link>" in manual["2.1"]
    guide = manual["7"]
    assert "\"(voorbereid)\" betekent: nog niet in Exact. Het staat op het tabblad In Exact invoeren." in guide
    assert "\"Postvak niet verbonden: meld u opnieuw aan op het tabblad Instellingen, onderaan bij Koppelingen\"" in guide and "meld je" not in whole
    for words in ("researcher applies", "applies those changes by hand", "onderzoeker voert", "onderzoeker draait", "changes to apply by hand"):
        assert words not in whole, words
    door = manual["3.10"]
    for words in ("`exact_base_url` stays empty", "Entity Services of Exact on a company server", "an Exact user for the aid with rights on purchase orders",
                  "the name of that server and of the database", "whether the Entity Services let a line of an existing purchase order be updated",
                  "implements the Writer protocol", "stands in front of the prepared writer", "never reaches the list"):
        assert words in door, words
    # the corrections of the settings' review round: the reasons as the page shows them, and the save note of the connections
    reasons = manual["2.4"]
    assert (f"| messages off for this supplier | {NL['reason:messages off for this supplier']} | {EN['reason:messages off for this supplier']} |"
            in reasons) and NL["reason:messages off for this supplier"] == NL["settings_supplier_messages_off"]
    assert "| new supplier | Nieuwe leverancier | New supplier | Until as many cases of this supplier are closed as" in reasons
    assert f"\"{NL['settings_buyer_saved']}\"" in manual["3.14"] and "De controle neemt dit over" not in whole
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    [needs] = [paragraph for paragraph in readme.split("\n\n") if "It answers only a request that names that host" in paragraph]
    assert not [name for name in ("Remy", "Johan", "Yijie") if name in needs] and "Remy" not in readme
    assert "the company's written permission" in needs and "What waits on the company's answer" not in readme
    assert "tab \"In Exact invoeren\"" in readme and "Microsoft sign-in on the settings tab" in readme
    assert "It answers only a request that names that host (127.0.0.1 or localhost, alone or with the page's port)" in readme
    assert "`--share` opens a public share link for the made-up set only: it is refused with company settings (exit code 2)." in readme


def test_the_documents_say_what_the_review_round_settled():
    """The review round in the documents: the block's button notes what the tab showed (Z1), an undo on the list (Z2), a list that
    cannot be read (Z4), what the buyer does in Exact himself (Z5), the article rows as the tab shows them (Z6), the rows one under
    the other with the buttons above and below (Z7), no name of a person and no waiting on an answer (Z8), and the wording of a
    change dropped and of a press that notes nothing (Z9)."""
    manual, whole = manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    guide = manual["7"]
    # Z1 and Z7
    assert "as the tab showed them when the list was last read" in manual["2.6"] and "stays on the list" in manual["2.6"]
    assert "one under the other" in manual["2.6"] and "above the rows and again below them" in manual["2.6"]
    # Z2: the undo of a change not entered takes it off the list, the undo of one entered asks the old value back
    for number in ("2.6", "3.10"):
        assert "off the list" in manual[number] and "the old value back" in manual[number], number
    assert f"\"{NL['hand_row:article_price_back'].format(item='KM-0801', old='12,70')}\"" in manual["3.10"]
    assert "off the list" in manual["4.3"]
    # Z2a: an undo takes away only its own case's request
    assert "A change that another case asked too stays on the list for that case." in manual["2.6"]
    assert "it takes away only its own case's request in that change" in manual["3.10"] and "for that case alone" in manual["3.10"]
    # Z4: the digest's line and the tab's label while the list cannot be read
    assert ("\"De lijst In Exact invoeren kon niet worden gelezen. Open het tabblad om te zien wat er nog in te voeren is: <link>\""
            in manual["2.1"])
    assert f"\"{NL['hand_tab_unread']}\"" in manual["2.6"] and "kon niet worden gelezen" in guide
    # Z5: the confirmation and the status are the buyer's to do in Exact, and the digest says so
    for number in ("2.1", "2.2", "2.6"):
        assert "attach the confirmation and set the status Bevestigd in Exact yourself" in manual[number], number
    assert "the buyer attaches the confirmation and sets the status Bevestigd in Exact himself" in manual["3.10"]
    assert "the buyer attaches the confirmation and sets the status Bevestigd in Exact himself" in readme
    assert "\"(zelf in Exact doen)\"" in manual["2.1"] and "\"in Exact zelf te doen\"" in manual["2.1"]
    assert "\"(zelf in Exact doen)\"" in guide and "voegt u zelf toe in Exact" in guide
    for words in ("the aid attaches the confirmation", "Then the aid attaches", "sets the PO to Bevestigd", "voegt de assistent de bevestiging toe",
                  "heeft de assistent al verwerkt", "every change it makes, and every change you confirm"):
        assert words not in whole and words not in readme, words
    # Z6: the article rows as the tab shows them
    assert "a row \"artikel <code>: inkoopprijs ...\" after the rows of the orders" in manual["2.2"] and "under \"Artikelprijzen\"" not in whole
    page_doc = Path("aid/review/page.py").read_text(encoding="utf-8").split('"""')[1]
    assert "and after them the article prices, each named \"artikel KM-0801:" in page_doc and "in blocks per purchase order" not in page_doc
    # Z8: roles, no names, and nothing waits on an answer
    assert "checks orders for the buyer. It does two checks." in manual["1"] and "bel of mail de onderzoeker.\n" in guide
    assert "The researcher decided on 2 November 2026" in readme and "docs/meetings/" in readme and "_Feedback_" not in readme
    for words in ("has not come yet", "his answer", "waits on", "'s answer"):
        assert words not in manual["3.1"] and words not in manual["3.3"], words
    assert "Microsoft sign-in on the settings tab" in manual["3.1"] and "the company's written permission" in manual["3.1"]
    assert "In Exact invoeren" in manual["3.1"] and "Microsoft sign-in on the settings tab" in manual["3.3"]
    # Z9
    assert "as long as no other value was asked for that line in between" in manual["2.6"]
    assert f"\"{NL['hand_nothing_noted']}\"" in manual["2.6"]


def test_the_language_switch_relabels_the_buttons_above_and_below_as_gradio_applies_it(tmp_path, madeup_out):
    """gradio takes the value out of an update as it applies it to its output, so one update given to two outputs would relabel only
    the first: each button of the tab gets its own update, and all of them read English after the switch (found in the browser)."""
    from gradio.blocks import postprocess_update_dict

    page, prepared, d = tab_page(tmp_path, madeup_out)
    [relabel] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "hand_relabel"]
    applied = [postprocess_update_dict(output, update) if isinstance(update, dict) else update
               for output, update in zip(relabel.outputs, relabel.fn("en", [], None))]
    assert [update["value"] for update in applied[6:]] == ["Refresh the list", "The ticked rows have been entered", "Do not enter the ticked rows",
                                                          "The ticked rows have been entered", "Do not enter the ticked rows",
                                                          "Everything of this block has been entered"]


def test_in_the_test_mode_the_tab_says_to_enter_the_changes_in_the_test_exact_only(tmp_path, madeup_out):
    """Amendment 1(a) of Task 33 (Task 34's rule 14): while test_mode is true the tab carries the line of the test mode right under its
    intro, in the page's language, also when nothing is to be entered; without the test mode the intro is as it was."""
    page, prepared, d = tab_page(tmp_path / "off", madeup_out)
    assert shown(page, "list_opened", "nl")["intro"] == NL["hand_intro"]
    page, prepared, d = tab_page(tmp_path / "on", madeup_out, test_mode=True)
    assert shown(page, "list_opened", "nl")["intro"] == f"{NL['hand_intro']}\n\n{NL['hand_test_mode']}"
    assert shown(page, "list_opened", "en")["intro"] == f"{EN['hand_intro']}\n\n{EN['hand_test_mode']}"
    for change in open_changes(prepared):
        mark(prepared, change.ids, "entered", datetime(2026, 11, 12, 10, 0))
    assert shown(page, "list_opened", "nl")["intro"] == f"{NL['hand_intro']}\n\n{NL['hand_test_mode']}\n\n{NL['hand_nothing']}"
    assert NL["hand_test_mode"] == "Proefstand: voer deze wijzigingen alleen in het test-Exact in."
    assert EN["hand_test_mode"] == "Test mode: enter these changes in the test Exact only."


def test_a_change_of_a_try_out_document_is_marked_in_and_out_of_the_test_mode(tmp_path, madeup_out):
    """Ruling Y4(d) of Task 33, finding 5: a change that only a try-out document asked carries "(proefdocument)" behind its row
    ("(try-out document)" in English), in the tick boxes and in its block, whether the test mode is on or off, so that a made-up change
    is never shown as one to enter in the real Exact; the changes of the suppliers' own confirmations carry nothing."""
    from aid.tryout import confirmation_pdf, made_from

    for mode in (True, False):
        page, prepared, d = tab_page(tmp_path / str(mode), madeup_out, test_mode=mode)
        made = tmp_path / str(mode) / "proef.pdf"
        made.write_bytes(confirmation_pdf(made_from(d.store, "250101"), "price_small", datetime(2026, 11, 12, 9, 30)))
        case = open_upload_case(d.settings.cases_root, [made], "250101", "confirmation", datetime(2026, 11, 12, 9, 30))
        record_dispositions(case, run_case(case, d))
        for language, mark_text in (("nl", "(proefdocument)"), ("en", "(try-out document)")):
            listed = shown(page, "list_opened", language)
            texts = [text for text, _ in listed["rows"]["choices"]]
            assert [text for text in texts if text.endswith(f" {mark_text}")] == [text for text in texts if "250101" in text or "KM-1001" in text]
            assert len([text for text in texts if text.endswith(f" {mark_text}")]) == 2, (mode, language)
        assert [text for text, _ in shown(page, "list_opened", "nl")["rows"]["choices"] if not text.endswith(" (proefdocument)")] == ROWS


# --- Task 35: the two sections under the list, the tick taken back and the put-back ---

DONE = ("done", "done_choice", "reopen", "put_back", "done_note", "confirmations")    # what done_opened gives, in the order of its outputs
DONE_HANDLERS = {"done_opened", "change_reopened", "change_put_back"}


def done_of(page, name: str, *args) -> dict:
    """What a handler of the sections under the list gives, by name: done_opened its six outputs, the two buttons the list's six as
    well, before them (the first function of that name; done_opened is bound to several events)."""
    found = [f.fn for f in page.fns.values() if getattr(f.fn, "__name__", "") == name][0]
    outputs = found(*args)
    if name == "done_opened":
        assert len(outputs) == len(DONE)
        return dict(zip(DONE, outputs))
    assert len(outputs) == len(SHOWN) + len(DONE)
    return dict(zip(SHOWN, outputs[:len(SHOWN)])) | dict(zip(DONE, outputs[len(SHOWN):]))


def chosen_of(sections: dict, words: str) -> str:
    """The value of the handled change whose text holds the words."""
    [value] = [value for text, value in sections["done_choice"]["choices"] if words in text]
    return value


def test_a_tick_and_a_drop_are_taken_back_and_a_seen_change_is_not(tmp_path, madeup_out):
    """'Weer op de lijst zetten' puts a change the buyer ticked or dropped himself on the list again (the mark reopened); for a change
    the aid saw in a newer purchase order it writes nothing and says to choose 'Terugdraaien in Exact'."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    listed = shown(page, "list_opened", "nl")
    price, date = value_of(listed, "prijs 12,50"), value_of(listed, "leverdatum")
    shown(page, "changes_entered", [price], "nl")
    shown(page, "changes_dropped", [date], "nl")
    sections = done_of(page, "done_opened", "nl", None)
    assert sections["done"] == ("**Afgehandeld in de laatste 14 dagen**\n\n"
                                "- Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70 (door u afgevinkt op 12-11-2026)\n"
                                "- Inkooporder 250114 (S14), regel 1 (KM-1401): leverdatum 16-11-2026 → week 48 (2026) (niet ingevoerd, op 12-11-2026)")
    assert [value for _, value in sections["done_choice"]["choices"]] == [price, date] and sections["done_choice"]["label"] == "Afgehandelde wijziging"
    again = done_of(page, "change_reopened", chosen_of(sections, "prijs 12,50"), "nl")
    assert again["done_note"] == "De regel staat weer op de lijst." and again["tab"]["label"] == "In Exact invoeren (2)"
    assert [text for text, _ in again["rows"]["choices"]] == [ROWS[0], ROWS[2]] and [v for _, v in again["done_choice"]["choices"]] == [date]
    back = done_of(page, "change_reopened", date, "en")
    assert back["done_note"] == "The row is on the list again." and back["tab"]["label"] == "Enter in Exact (3)"
    assert back["done"] == "**Handled in the last 14 days**" and back["done_choice"]["choices"] == []
    marks = [json.loads(line) for line in (prepared / MARKS_FILE).read_text(encoding="utf-8").splitlines()]
    assert [(line["entry_id"], line["mark"]) for line in marks[-2:]] == [(ids_of(price)[0], "reopened"), (ids_of(date)[0], "reopened")]
    # a change the aid saw itself is not taken back: nothing is written, and the note says what to do instead
    mark(prepared, ids_of(price), "seen", datetime(2026, 11, 12, 10, 0), by="version 2")
    sections = done_of(page, "done_opened", "nl", None)
    assert sections["done"].endswith("- Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70 (gezien in versie 2 van de inkooporder)")
    before = (prepared / MARKS_FILE).read_bytes()
    for language in ("nl", "en"):
        refused = done_of(page, "change_reopened", price, language)
        assert refused["done_note"] == STRINGS[language]["hand_reopen_seen"] and refused["tab"]["label"] == STRINGS[language]["hand_tab_count"].format(n=2)
    assert NL["hand_reopen_seen"] == ("Deze wijziging heeft de assistent zelf in Exact gezien. Kies Terugdraaien in Exact als de oude waarde "
                                      "terug moet.")
    assert (prepared / MARKS_FILE).read_bytes() == before
    assert done_of(page, "change_reopened", None, "nl")["done_note"] == "Kies eerst een wijziging bij Afgehandelde wijziging."   # nothing chosen
    assert done_of(page, "change_put_back", "", "en")["done_note"] == "Choose a change under Handled change first."               # (K8)
    assert (prepared / MARKS_FILE).read_bytes() == before


def test_the_put_back_note_never_says_undone(tmp_path, madeup_out):
    """'Terugdraaien in Exact' prepares the old value: the note says that it is not put back in Exact yet and names the row the list now
    holds, never that the change is undone, and the change stays among the handled ones. The review tab's own undo, which the page's
    writer only prepares, says the same."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    price = value_of(shown(page, "list_opened", "nl"), "prijs 12,50")
    shown(page, "changes_entered", [price], "nl")
    put = done_of(page, "change_put_back", price, "nl")
    assert put["done_note"] == ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): "
                                "prijs 12,70 → 12,50. Voer de oude waarde in Exact in en vink de regel af.")
    assert "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50" in [text for text, _ in put["rows"]["choices"]]
    assert put["tab"]["label"] == "In Exact invoeren (3)" and [value for _, value in put["done_choice"]["choices"]] == [price]
    assert done_of(page, "change_put_back", price, "en")["done_note"] == "The put-back is on the list already."
    # the review tab: a write made in a system, undone through the page's prepared writer, is a put-back on the list as well
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    made = WriteLogEntry(entry_id="0" * 31 + "1", case_id=case_id, status="done", previous="50", new="60", rule_id="B01", evidence={},
                         op=WriteOp(kind="po_line_quantity", po_number="250108", version=1, line_id="2", old_value="50", new_value="60"),
                         timestamp="2026-11-12T09:30:00")
    WriteLog(d.settings.cases_root / case_id).append(made)
    text, _ = handler(page, "do_undo")(case_id, made.entry_id, "nl")
    assert text == ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: Inkooporder 250108 (S08), Terugzetten: regel 2 (KM-0802): aantal "
                    "60 → 50. Voer de oude waarde in Exact in en vink de regel af.")
    for words in (put["done_note"], text, done_of(page, "change_put_back", price, "nl")["done_note"]):
        assert "ngedaan" not in words and "ndone" not in words
    assert NL["undone"].startswith("Ongedaan gemaakt")                       # kept for a write a writer made undone in a system


def test_the_sections_under_the_list_are_wired(tmp_path, madeup_out):
    """Under the list: the section of the handled changes (its text, the dropdown 'Afgehandelde wijziging' with the two buttons, a note)
    and the section of the confirmations, fixed controls each. done_opened reads both when the page loads, when the tab is selected,
    with 'Lijst vernieuwen', when the language switches and after each press of the list; the two buttons give the list and the
    sections."""
    gr = import_gradio()
    page, prepared, d = tab_page(tmp_path, madeup_out)
    tab = hand_tab(page)
    language = [block for block in page.blocks.values() if isinstance(block, gr.Radio) and block.label == NL["language"]][0]
    order = [block for block in inside(tab) if isinstance(block, (gr.Markdown, gr.Button, gr.CheckboxGroup, gr.Dropdown))]
    names = [getattr(block, "elem_id", None) or getattr(block, "label", None) or block.value for block in order]
    assert names[-6:] == ["hand-done", "Afgehandelde wijziging", "Weer op de lijst zetten", "Terugdraaien in Exact", "hand-done-note",
                          "hand-confirmations"]
    done_md, choice, reopen, put_back, note, confirmations_md = order[-6:]
    fills = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "done_opened"]
    outputs = [done_md, choice, reopen, put_back, note, confirmations_md]
    assert all(f.outputs == outputs and f.inputs == [language, choice] for f in fills)
    [list_fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "list_opened"]
    presses = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") in ("changes_entered", "changes_dropped", "block_entered")]
    triggered = sorted(target for f in fills for target in f.targets if target[0] is not None)
    assert triggered == sorted([*list_fill.targets, (language._id, "change")])
    after = sorted(f.trigger_after for f in fills if f.targets == [(None, "then")])
    assert after == sorted(key for key, f in page.fns.items() if f in presses)
    for name, button in (("change_reopened", reopen), ("change_put_back", put_back)):
        [f] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == name]
        assert f.targets == [(button._id, "click")] and f.inputs == [choice, language] and f.outputs == [*list_fill.outputs, *outputs]
    english = done_of(page, "done_opened", "en", None)
    assert (english["done_choice"]["label"], english["reopen"]["value"], english["put_back"]["value"]) == (
        "Handled change", "Put on the list again", "Put back in Exact")
    assert english["confirmations"].startswith("**Confirmed by the supplier (last 7 days): attach the confirmation in Exact and set the status "
                                               "to Bevestigd**\n\n- 12-11-2026: purchase order 250108 (S08), file MU08\\_conf.pdf")


def test_the_confirmations_section_lists_what_to_attach_in_exact(tmp_path, madeup_out):
    page, prepared, d = tab_page(tmp_path, madeup_out)
    sections = done_of(page, "done_opened", "nl", None)
    assert sections["confirmations"] == ("**Bevestigd door de leverancier (laatste 7 dagen): voeg in Exact de bevestiging bij en zet de status "
                                         "op Bevestigd**\n\n"
                                         "- 12-11-2026: inkooporder 250108 (S08), bestand MU08\\_conf.pdf\n"
                                         "- 12-11-2026: inkooporder 250114 (S14), bestand MU14\\_conf.pdf")
    assert sections["done"] == "**Afgehandeld in de laatste 14 dagen**" and sections["done_note"] == ""
    late = d.model_copy(update={"clock": lambda: datetime(2026, 11, 20, 9, 0)})
    later_page = build_app(late, settings_file=tmp_path / "settings.json")
    assert done_of(later_page, "done_opened", "nl", None)["confirmations"] == ""                     # more than 7 days later: none


def test_the_texts_of_the_sections_in_both_languages():
    brief = {"hand_put_back_row": ("Terugzetten: {row}", "Put back: {row}"),
             "hand_exact_shows": ("Exact toont nu {value}.", "Exact now shows {value}."),
             "hand_done": ("Afgehandeld in de laatste 14 dagen", "Handled in the last 14 days"),
             "hand_done_ticked": ("(door u afgevinkt op {date})", "(ticked by you on {date})"),
             "hand_done_seen": ("(gezien in versie {n} van de inkooporder)", "(seen in version {n} of the purchase order)"),
             "hand_done_dropped": ("(niet ingevoerd, op {date})", "(not entered, on {date})"),
             "hand_reopen": ("Weer op de lijst zetten", "Put on the list again"),
             "hand_put_back": ("Terugdraaien in Exact", "Put back in Exact"),
             "hand_done_choice": ("Afgehandelde wijziging", "Handled change"),
             "hand_reopened": ("De regel staat weer op de lijst.", "The row is on the list again."),
             "hand_reopen_seen": ("Deze wijziging heeft de assistent zelf in Exact gezien. Kies Terugdraaien in Exact als de oude waarde terug moet.",
                                  "The assistant saw this change in Exact itself. Choose Put back in Exact if the old value must return."),
             "hand_put_back_listed": ("Nog niet teruggedraaid in Exact. Op de lijst staat nu: {row}. Voer de oude waarde in Exact in en vink de regel af.",
                                      "Not put back in Exact yet. The list now holds: {row}. Enter the old value in Exact and tick the row."),
             "hand_put_back_on_list": ("Het terugzetten staat al op de lijst.", "The put-back is on the list already."),
             "hand_put_back_done": ("Deze wijziging is al teruggedraaid.", "This change was put back already."),
             "hand_put_back_unknown": ("Van dit artikel kent de assistent de oude prijs niet; zet die zelf terug in Exact.",
                                       "The assistant does not know the old price of this article; put it back in Exact yourself."),
             "hand_confirmations": ("Bevestigd door de leverancier (laatste 7 dagen): voeg in Exact de bevestiging bij en zet de status op Bevestigd",
                                    "Confirmed by the supplier (last 7 days): attach the confirmation in Exact and set the status to Bevestigd"),
             "hand_confirmation": ("{date}: inkooporder {number} ({supplier}), bestand {file}", "{date}: purchase order {number} ({supplier}), file {file}"),
             "hand_attach_only": ("alleen bijvoegen: er staat nog iets open", "attach only: something is still open"),
             # the review round's rulings K4, K8 and K9, and the second round's K10 and K13 (which replaced K2's one note by two)
             "hand_put_back_newer_open": ("Terugdraaien kan nu niet: er staat een nieuwere wijziging op de lijst, {row}. Laat die eerst "
                                          "vallen; daarna kunt u deze wijziging terugdraaien.",
                                          "Cannot put back now: a newer change is on the list, {row}. Drop that one first; then you can "
                                          "put this change back."),
             "hand_put_back_newer_entered": ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, {row}. Draai die eerst terug "
                                             "en vink het terugzetten af; daarna kunt u deze wijziging terugdraaien.",
                                             "Cannot put back now: a newer change was entered, {row}. Put that one back first and tick its "
                                             "put-back off; then you can put this change back."),
             "hand_put_back_lost": ("Terugdraaien kan nu niet: het terugzetten zou niet op de lijst komen. Er is niets geschreven; geef dit "
                                    "door aan de onderzoeker.",
                                    "Cannot put back now: the put-back would not reach the list. Nothing was written; pass this on to the "
                                    "researcher."),
             # the third round's K21 and K23
             "hand_put_back_not_needed": ("Terugdraaien is niet nodig: op de lijst staat al een wijziging naar de oude waarde, {row}. Voer die "
                                          "in Exact in en vink die af.",
                                          "No need to put back: the list already holds a change to the old value, {row}. Enter it in Exact "
                                          "and tick it off."),
             "hand_put_back_newer_never": ("Terugdraaien kan nu niet: er is een nieuwere wijziging ingevoerd, {row}, die de assistent niet kan "
                                           "terugdraaien. Zet de oude waarde zelf terug in Exact.",
                                           "Cannot put back now: a newer change was entered, {row}, which the assistant cannot put back. Put "
                                           "the old value back in Exact yourself."),
             "hand_reopen_put_back": ("Deze wijziging is teruggedraaid; zij komt niet terug op de lijst.",
                                      "This change was put back; it does not come back on the list."),
             "hand_done_choose_first": ("Kies eerst een wijziging bij Afgehandelde wijziging.", "Choose a change under Handled change first."),
             "hand_done_seen_export": ("(gezien in een export uit Exact)", "(seen in an export from Exact)")}
    assert {key: (NL[key], EN[key]) for key in brief} == brief
    # beyond the brief: an order whose supplier is unknown, and three notes for what the brief leaves open
    beyond = {"hand_confirmation_alone": ("{date}: inkooporder {number}, bestand {file}", "{date}: purchase order {number}, file {file}"),
              "hand_put_back_not_entered": ("Deze wijziging is niet ingevoerd; in Exact is er niets terug te draaien.",
                                            "This change was not entered; there is nothing to put back in Exact."),
              "hand_put_back_cannot": ("De assistent kan deze wijziging niet terugdraaien; zet de oude waarde zelf terug in Exact.",
                                       "The assistant cannot put this change back; put the old value back in Exact yourself."),
              "hand_reopen_not": ("De wijziging komt niet terug op de lijst: er is intussen een andere wijziging voor dezelfde regel of hetzelfde artikel.",
                                  "The change does not return to the list: there is another change for the same line or article by now.")}
    assert {key: (NL[key], EN[key]) for key in beyond} == beyond
    for strings in (NL, EN):
        assert not [key for key in [*brief, *beyond] if re.search(r"\bR\d{2}\b|—|–|ngedaan|ndone", strings[key])]
    assert "hand_put_back_newer" not in NL and "hand_put_back_newer" not in EN          # replaced by its two notes (K13)


def test_the_documents_say_what_task_35_added():
    """The manual's 2.6 quotes the sections under the list and their notes word for word, 3.10 says how a new PDF ticks a change off
    and how a put-back is made and recorded, and the Dutch guide has the sentence of the brief; no sentence says any more that a tick
    cannot be taken back on the page or that the confirmations are listed nowhere."""
    manual, whole = manual_sections(), Path("aid/MANUAL.md").read_text(encoding="utf-8")
    for key in ("hand_done", "hand_done_choice", "hand_reopen", "hand_put_back", "hand_reopened", "hand_reopen_seen", "hand_reopen_not",
                "hand_put_back_on_list", "hand_put_back_done", "hand_put_back_unknown", "hand_put_back_not_entered", "hand_put_back_cannot",
                "hand_confirmations"):
        assert f"\"{NL[key]}\"" in manual["2.6"], key
    for words in ("(door u afgevinkt op 18-11-2026)", "(gezien in versie 2 van de inkooporder)", "(niet ingevoerd, op 18-11-2026)",
                  "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50",
                  "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70. Exact toont nu 12,80.",
                  NL["hand_put_back_listed"].format(row="Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"),
                  "18-11-2026: inkooporder 250108 (S08), bestand MU08_conf.pdf", f"({NL['hand_attach_only']})", "a day inside the new week counts"):
        assert words in manual["2.6"], words
    for words in ("`reconcile` of `aid/execution/by_hand.py`", "\"version <n>\"", "\"list of changes\"", "`undo` of `aid/review/close.py`",
                  "`undo_entry`", "`<cases_root>/pricelists/`", '"mark": "puts_back"', "check A7"):
        assert words in manual["3.10"], words
    assert ("Ziet de assistent uw wijziging in een nieuwe inkooporder-pdf uit Exact, dan vinkt hij haar zelf af; anders vinkt u haar af."
            in manual["7"])
    assert "\"Weer op de lijst zetten\"" in manual["7"] and "\"Terugdraaien in Exact\"" in manual["7"]
    for words in ("A tick cannot be taken back on the page", "niet terugnemen", "lists them nowhere yet", "on no list yet",
                  "nog op geen lijst", "which it lists nowhere yet"):
        assert words not in whole, words
    assert "\"Nog niet teruggedraaid in Exact. Op de lijst staat nu: ...\"" in manual["2.2"]


def test_a_put_back_the_writer_cannot_prepare_is_named_by_its_error_and_writes_nothing(tmp_path, madeup_out):
    """A writer that cannot prepare the put-back (its folder cannot be written) leaves everything as it was: the note names the class
    of the error, the list is shown as it is, and no put-back is linked."""
    from aid.execution.writer import WriterError

    class Refusing:
        name = "refusing"

        def apply(self, *args):
            raise WriterError("made-up: the folder cannot be written")

    page, prepared, d = tab_page(tmp_path, madeup_out)
    price = value_of(shown(page, "list_opened", "nl"), "prijs 12,50")
    shown(page, "changes_entered", [price], "nl")
    refusing = build_app(d.model_copy(update={"writer": Refusing()}), settings_file=tmp_path / "settings.json")
    before = (prepared / MARKS_FILE).read_bytes()
    put = done_of(refusing, "change_put_back", price, "nl")
    assert put["done_note"] == "Dit is niet gelukt (WriterError)." and put["tab"]["label"] == "In Exact invoeren (2)"
    assert (prepared / MARKS_FILE).read_bytes() == before and [value for _, value in put["done_choice"]["choices"]] == [price]


# --- the review round of Task 35 ---

def test_a_tick_taken_back_after_its_put_back_says_that_the_change_was_put_back(tmp_path, madeup_out):
    """The reviewers' sequence (K4): the buyer ticks MU08's price, puts it back, drops the put-back and takes the tick back. The change
    does not come back on the list, since its put-back would cancel it (the buyer asked for the old value); nothing is written, and the
    note says so truly. The note about another change is given only when another change of the line really came in."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    price = value_of(shown(page, "list_opened", "nl"), "prijs 12,50")
    shown(page, "changes_entered", [price], "nl")
    done_of(page, "change_put_back", price, "nl")
    put = value_of(shown(page, "list_opened", "nl"), "Terugzetten")
    shown(page, "changes_dropped", [put], "nl")
    before = (prepared / MARKS_FILE).read_bytes()
    for language, said in (("nl", "Deze wijziging is teruggedraaid; zij komt niet terug op de lijst."),
                           ("en", "This change was put back; it does not come back on the list.")):
        taken = done_of(page, "change_reopened", price, language)
        assert taken["done_note"] == said and price not in [value for _, value in taken["rows"]["choices"]]
    assert (prepared / MARKS_FILE).read_bytes() == before
    other, other_prepared, other_deps = tab_page(tmp_path / "other", madeup_out)
    ticked = value_of(shown(other, "list_opened", "nl"), "prijs 12,50")
    shown(other, "changes_entered", [ticked], "nl")
    [case_id] = [path.name for path in other_deps.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    PreparedWriter(other_prepared).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50",
                                                 new_value="12.90"), case_id, "R04", {}, datetime(2026, 11, 12, 9, 30))   # a newer change
    assert done_of(other, "change_reopened", ticked, "nl")["done_note"] == NL["hand_reopen_not"]


def test_the_documents_say_what_the_review_round_of_task_35_settled():
    """The manual after the review round: a look counts only when it came after the change (K1, 3.10 and 2.6), a put-back never
    replaces a newer change (K2), the pre-check says that a put-back waits on the list (K3, 2.5), the take-back that meets its own
    put-back (K4), the 7 days of the confirmations (K5, 2.1, 2.2 and 2.6), reopen() in 3.10 (K6), and the wording of K8 and K9."""
    manual = manual_sections()
    two_six, three_ten, guide = manual["2.6"], manual["3.10"], manual["7"]
    assert "filed after the newest row of the change was prepared" in three_ten and "nor the version of the change" not in three_ten
    assert "counts only when the aid filed it after the change was asked" in two_six
    assert "\"terugzetten staat op de lijst In Exact invoeren\"" in manual["2.5"]
    assert f"\"{NL['hand_reopen_put_back']}\"" in two_six and "only when another change for that line or article came in" in two_six
    for number in ("2.1", "2.2", "2.6"):
        assert "7 days after the aid last prepared the attachment or the status" in manual[number], number
    assert "`reopen(" in three_ten and '"reopened", datetime.now())' not in three_ten and "writes the mark whatever the state" in three_ten
    assert f"\"{NL['hand_done_choose_first']}\"" in two_six
    assert "stays among the handled ones as it was, next to its put-back" in two_six and f"\"{NL['hand_put_back_done']}\"" in two_six
    assert ("came in after the put-back, it replaced the put-back on the list, also one you had dropped, as a newer value replaces any "
            "older change; while that newer change is on the list, or is entered or seen and Exact still holds it, a press says "
            f"\"{NL['hand_reopen_not']}\"") in two_six                                                         # (K22)
    assert f"\"{NL['hand_done_seen_export']}\"" in two_six
    assert "kies die dan bij \"Afgehandelde wijziging\"" in guide and "kies haar dan" not in guide and '.".' not in guide
    assert "tot dan blijft de wijziging zelf als ingevoerd staan" not in guide and "naast haar terugzetregel" in guide


# --- the second review round of Task 35 ---

def test_a_tick_of_a_change_two_confirmations_asked_is_not_taken_back_after_its_put_back(tmp_path, madeup_out):
    """The checkers' sequence through the page's handlers (second review round, K11): MU08's confirmation came in twice, so its price
    is one row of two cases. The buyer ticks it and presses "Terugdraaien in Exact"; "Weer op de lijst zetten" on the change then says
    K4's note in both languages and writes nothing, and the put-back stays on the list; the same once the put-back was dropped."""
    page, prepared, d = tab_page(tmp_path, madeup_out)
    again = open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", datetime(2026, 11, 12, 9, 30))
    record_dispositions(again, run_case(again, d))
    price = value_of(shown(page, "list_opened", "nl"), "prijs 12,50")
    assert len(price.split("+")) == 2                                          # one row, the entries of two cases
    shown(page, "changes_entered", [price], "nl")
    assert done_of(page, "change_put_back", price, "nl")["done_note"].startswith("Nog niet teruggedraaid in Exact.")
    row = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    for dropped in (False, True):
        if dropped:
            shown(page, "changes_dropped", [value_of(shown(page, "list_opened", "nl"), "Terugzetten")], "nl")
        before = (prepared / MARKS_FILE).read_bytes()
        for language, said in (("nl", "Deze wijziging is teruggedraaid; zij komt niet terug op de lijst."),
                               ("en", "This change was put back; it does not come back on the list.")):
            taken = done_of(page, "change_reopened", price, language)
            assert taken["done_note"] == said, (dropped, language)
        assert (row in [text for text, _ in shown(page, "list_opened", "nl")["rows"]["choices"]]) is not dropped
        assert "prijs 12,50 → 12,70" not in " ".join(text for text, _ in shown(page, "list_opened", "nl")["rows"]["choices"])
        assert (prepared / MARKS_FILE).read_bytes() == before
        assert [(change.new_value, change.state) for change in changes(prepared)[0] if change.kind == "po_line_price"] == [
            ("12.70", "entered"), ("12.50", "dropped" if dropped else "open")]


def test_the_documents_say_what_the_second_review_round_settled():
    """The manual and by_hand's docstring after the second review round: K2's refusal says how it lifts (K13, 2.6 and the Dutch guide),
    the guard of K10, the time that counts for a look is when the aid filed it (K14, 2.6, 3.10 and the docstring), the export beside
    the new PDF (K16, 2.1, 2.6 and Change.marked_by) and the 7 days per confirmation (K17)."""
    import inspect

    import aid.execution.by_hand as by_hand

    manual = manual_sections()
    two_one, two_six, three_ten, guide = manual["2.1"], manual["2.6"], manual["3.10"], manual["7"]
    newer = "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,70 → 12,90"
    for key in ("hand_put_back_newer_open", "hand_put_back_newer_entered"):
        assert f"\"{NL[key].format(row=newer)}\"" in two_six, key
    assert "Handel die eerst af" not in two_six and "handel die dan eerst af" not in guide
    assert "once its own put-back is entered or seen" in two_six
    assert "laat die dan eerst vallen" in guide and "draai die dan eerst terug en vink het terugzetten af" in guide
    assert f"\"{NL['hand_put_back_lost']}\"" in two_six
    docstring = " ".join(by_hand.__doc__.split())                             # its lines wrapped
    for text in (two_six, three_ten, docstring):
        assert "counts as new" in text and ("when the aid filed" in text or "when the store filed" in text)
    assert "never ticks it off" not in two_six.split("**Seen by the aid itself.**")[1].split("\n")[0]
    assert "never by one from before the buyer entered the change" not in three_ten
    assert "no print that Exact made before the buyer entered the change" not in docstring
    assert "check in Exact" in two_six and "put-backs it ticked off as seen" in two_six
    tick = next(line for line in two_six.splitlines() if line.startswith("**What a tick is.**"))
    handled = next(line for line in two_six.splitlines() if line.startswith("**Handled changes.**"))
    assert "export" in tick and '"export"' in tick and f"\"{NL['hand_done_seen_export']}\"" in handled
    assert "in a new PDF of the purchase order or in an export from Exact" in two_one
    assert '"export"' in inspect.getsource(by_hand.Change)
    assert "for that confirmation" in two_six and "for that order" not in two_six


# --- the third review round of Task 35 ---

def test_the_documents_say_what_the_third_review_round_settled():
    """The manual after the third review round: 2.5 says what happens while the list cannot be read (K24, since the fourth round
    K30's sentence); 2.6's advice after a stop names a lapsed sign-in of the mailbox (K24); 2.6 and the Dutch guide say what K20 to
    K23 make true, and the promises that no longer hold are gone."""
    manual = manual_sections()
    two_five, two_six, guide = manual["2.5"], manual["2.6"], manual["7"]
    assert "While the list cannot be read" in two_five and "amber" in two_five
    assert "the sign-in of the mailbox had lapsed, check in Exact" in two_six
    newer = "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,70 → 12,50"
    assert f"\"{NL['hand_put_back_not_needed'].format(row=newer)}\"" in two_six
    article = "artikel KM-0801: inkoopprijs onbekend → 12,90"
    assert f"\"{NL['hand_put_back_newer_never'].format(row=article)}\"" in two_six
    assert "A put-back is a row of its own" in two_six and "makes a new put-back" in two_six
    assert "unless the marks say that Exact holds this change's old value already" in two_six
    assert "or once the marks say that Exact no longer holds its value" in two_six
    assert "as Exact then holds this change's value again" not in two_six
    assert "voer die dan in Exact in en vink die af" in guide and "zet de oude waarde dan zelf terug in Exact" in guide


def test_the_documents_say_what_the_fourth_review_round_settled():
    """The manual and the writer's docstring after the fourth review round: 2.5 says that while the list cannot be read no prepared
    change counts as made, with the line's reason (K30), in place of the sentence that only a waiting put-back counts as entered; 2.6's
    advice after a stop or a lapsed sign-in says when to check (K33); 3.10, in both its paragraphs on the undo, and the writer's
    docstring say that "Terugdraaien in Exact" undoes an entry a second time (K33), and 3.10 names the recipe's limits (K33)."""
    import aid.execution.writer as writer

    manual = manual_sections()
    two_five, two_six, three_ten = manual["2.5"], manual["2.6"], manual["3.10"]
    assert "no prepared change counts as made" in two_five and '"de lijst In Exact invoeren kon niet worden gelezen"' in two_five
    assert "a put-back that waits counts as entered" not in two_five
    assert "check in Exact, once it runs again or the sign-in works again, the put-backs it ticked off as seen" in two_six
    assert "at its next start" not in two_six
    second = "undoes a change a second time once its first put-back was replaced or dropped and nothing newer stands in the way"
    undo, tab = (paragraph for paragraph in three_ten.split("\n\n") if paragraph.startswith(("**The undo.**", "**Put back from the tab.**")))
    assert second in undo and "Each change is undone once." not in undo
    assert second in tab and "a second press does not prepare it again." not in tab
    assert ("an undo made with it that a later case's request for the same value joins, or whose change's tick is taken back, may be "
            "passed over by the list; prefer \"Terugdraaien in Exact\" on the tab") in three_ten
    doc = " ".join((writer.__doc__ or "").split())
    assert "undoes an entry a second time once its first put-back was replaced or dropped and nothing newer stands in the way" in doc
