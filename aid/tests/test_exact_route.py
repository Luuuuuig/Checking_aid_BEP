"""Tests of the plug point of a route into Exact (aid/execution/exact_route.py) and of the button "Aangevinkte regels in Exact zetten"
of the tab "In Exact invoeren" (aid/review/page.py), after the design of 17 November
(docs/superpowers/specs/2026-11-17-click-to-exact-design.md). The plug point gives no route in this version, so the page shows no button;
the tests give build_app a made-up stand-in route, which writes nothing anywhere, and run the tab's handlers by name as
test_hand_tab.py does: no browser, no server, no call to any service."""
import json
import os
import re
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aid.execution.by_hand import MARKS_FILE, ListUnread, changes, mark, open_changes
from aid.execution.ops import WriteOp
from aid.execution.writer import PreparedWriter, WriteLog, WriteLogEntry
from aid.pipeline import record_dispositions, run_case
from aid.review.hand_list import ids_of
from aid.review.i18n import STRINGS
from aid.review.page import build_app, import_gradio
from aid.settings import load_settings
from aid.sources.upload import open_upload_case
from aid.tests.conftest import SETTINGS, deps
from aid.tests.test_hand_tab import EN, NL, PO, Clock, chosen_of, done_of, hand_tab, handler, inside, manual_sections, settings_file
from aid.tests.test_hand_tab import shown as listed_by
from aid.tests.test_hand_tab import value_of

LOG = "exact_writes.jsonl"                       # the route's log, under <cases_root>/prepared
WRITE = "changes_written"                        # the handler of the button
# The texts of the button and of what it says, in both languages: the literal copies of aid/review/i18n.py.
TEXTS = {
    "hand_exact": ("Aangevinkte regels in Exact zetten", "Put the ticked rows into Exact"),
    "hand_intro_route": ("De assistent zet alleen iets in Exact als u daarom vraagt. Vink de regels aan en druk op Aangevinkte regels in "
                         "Exact zetten, of voer de wijzigingen zelf in Exact in en vink aan wat u hebt ingevoerd.",
                         "The assistant puts something into Exact only when you ask it to. Tick the rows and press Put the ticked rows "
                         "into Exact, or enter the changes in Exact yourself and tick what you have entered."),
    "hand_exact_written_one": ("1 wijziging in Exact gezet.", "1 change put into Exact."),
    "hand_exact_written_many": ("{n} wijzigingen in Exact gezet.", "{n} changes put into Exact."),
    "hand_exact_unmarked_one": ("1 wijziging staat in Exact, maar de regel is niet afgevinkt: hij stond niet meer open, of de lijst kon niet "
                                "worden bijgewerkt. Staat de regel nog op de lijst, vink hem dan zelf af.",
                                "1 change is in Exact, but its row was not ticked off: it was no longer open, or the list could not be "
                                "updated. If the row is still on the list, tick it off yourself."),
    "hand_exact_unmarked_many": ("{n} wijzigingen staan in Exact, maar de regels zijn niet afgevinkt: zij stonden niet meer open, of de lijst "
                                 "kon niet worden bijgewerkt. Staan de regels nog op de lijst, vink ze dan zelf af.",
                                 "{n} changes are in Exact, but their rows were not ticked off: they were no longer open, or the list could "
                                 "not be updated. If the rows are still on the list, tick them off yourself."),
    "hand_exact_not_made_one": ("1 wijziging is niet in Exact gezet.", "1 change was not put into Exact."),
    "hand_exact_not_made_many": ("{n} wijzigingen zijn niet in Exact gezet.", "{n} changes were not put into Exact."),
    "hand_exact_unsure_one": ("Van 1 wijziging is niet zeker of zij in Exact staat: kijk in Exact en vink de regel af als zij er staat.",
                              "It is not certain whether 1 change is in Exact: check in Exact and tick the row off if it is there."),
    "hand_exact_unsure_many": ("Van {n} wijzigingen is niet zeker of zij in Exact staan: kijk in Exact en vink af wat er staat.",
                               "It is not certain whether {n} changes are in Exact: check in Exact and tick off what is there."),
    "hand_exact_handed_over_one": ("1 wijziging is aan Exact doorgegeven en nog niet verwerkt: kijk later in Exact en vink de regel af als "
                                   "zij er staat.",
                                   "1 change was handed over to Exact and has not been applied yet: check Exact later and tick the row off "
                                   "once it is there."),
    "hand_exact_handed_over_many": ("{n} wijzigingen zijn aan Exact doorgegeven en nog niet verwerkt: kijk later in Exact en vink af wat er "
                                    "staat.",
                                    "{n} changes were handed over to Exact and have not been applied yet: check Exact later and tick off "
                                    "what is there."),
    "hand_exact_passed_one": ("1 aangevinkte regel is overgeslagen: de assistent zet die niet zelf in Exact. Staat de regel nog op de lijst, "
                              "handel hem dan zelf af.",
                              "1 ticked row was passed over: the assistant does not put it into Exact itself. If the row is still on the "
                              "list, handle it yourself."),
    "hand_exact_passed_many": ("{n} aangevinkte regels zijn overgeslagen: de assistent zet die niet zelf in Exact. Staan de regels nog op de "
                               "lijst, handel ze dan zelf af.",
                               "{n} ticked rows were passed over: the assistant does not put them into Exact itself. If the rows are still "
                               "on the list, handle them yourself."),
    "hand_exact_stop:settings": ("De instellingen kunnen nu niet worden gelezen: de assistent zet niets in Exact. Geef dit door aan de "
                                 "onderzoeker.",
                                 "The settings cannot be read now: the assistant puts nothing into Exact. Pass this on to the researcher."),
    "hand_exact_stop:test_mode": ("De assistent zet alleen in de proefstand iets in Exact, en dan alleen in het test-Exact; de proefstand "
                                  "staat uit.",
                                  "The assistant puts changes into Exact in the test mode only, and then into the test Exact only; the "
                                  "test mode is off."),
    "hand_exact_stop:live": ("De koppeling leidt naar het echte Exact, en daarin zet de assistent niets.",
                             "The connection points to the live Exact; the assistant puts nothing into it."),
    "hand_exact_stop:unavailable": ("De koppeling met Exact is nu niet beschikbaar. Probeer het later opnieuw, of voer de wijzigingen zelf "
                                    "in Exact in.",
                                    "The connection to Exact is not available now. Try again later, or enter the changes in Exact yourself."),
    "hand_exact_stop:log": ("Het logboek van de koppeling met Exact kan nu niet worden gelezen of geschreven: de assistent zet niets in "
                            "Exact. Geef dit door aan de onderzoeker.",
                            "The log of the connection to Exact cannot be read or written now: the assistant puts nothing into Exact. "
                            "Pass this on to the researcher."),
    "hand_exact_unsure_row": ("Niet zeker of dit in Exact staat: kijk in Exact en vink de regel af als het er staat.",
                              "It is not certain whether this is in Exact: check in Exact and tick the row off if it is there."),
    "hand_exact_handed_over_row": ("Aan Exact doorgegeven en nog niet verwerkt: kijk later in Exact en vink de regel af als het er staat.",
                                   "Handed over to Exact but not applied yet: check Exact later and tick the row off once it is there."),
    "hand_done_written": ("(door de assistent in Exact gezet op {date})", "(put into Exact by the assistant on {date})"),
    "hand_reopen_written": ("Deze wijziging heeft de assistent zelf in Exact gezet. Kies Terugdraaien in Exact als de oude waarde terug "
                            "moet.",
                            "The assistant put this change into Exact itself. Choose Put back in Exact if the old value must return."),
    # the review round of 17 November: the safety items
    "hand_exact_stop:error": ("Er ging iets onverwachts mis: de assistent zet nu niets meer in Exact. Geef dit door aan de onderzoeker.",
                              "Something unexpected went wrong: the assistant has stopped putting changes into Exact. Pass this on to the "
                              "researcher."),
    "hand_exact_replaced_one": ("1 wijziging is in Exact gezet, maar intussen staat er een nieuwere wijziging voor dezelfde regel of "
                                "hetzelfde artikel op de lijst, met een andere waarde: voer die in Exact in of laat die vallen.",
                                "1 change was put into Exact, but by now a newer change for the same line or article is on the list, "
                                "with another value: enter that one in Exact or drop it."),
    "hand_exact_replaced_many": ("{n} wijzigingen zijn in Exact gezet, maar intussen staan er nieuwere wijzigingen voor dezelfde regels of "
                                 "artikelen op de lijst, met andere waarden: voer die in Exact in of laat ze vallen.",
                                 "{n} changes were put into Exact, but by now newer changes for the same lines or articles are on the "
                                 "list, with other values: enter those in Exact or drop them."),
    "hand_exact_unticked_row": ("De assistent heeft dit in Exact gezet, maar kon de regel niet afvinken: vink hem zelf af.",
                                "The assistant put this into Exact, but could not tick the row off: tick it off yourself."),
    "hand_exact_put_back_hand_row": ("Dit terugzetten voert de assistent niet zelf in Exact in: voer het zelf in en vink de regel af.",
                                     "The assistant does not enter this put-back in Exact itself: enter it yourself and tick the row off."),
    "hand_exact_put_back_other_row": ("Dit terugzetten voert de assistent niet zelf in Exact in. Voordat de assistent de wijziging in Exact "
                                      "zette, stond daar {value}. Kijk in Exact welke waarde terug moet, zet die zelf terug en vink de regel "
                                      "af.",
                                      "The assistant does not enter this put-back in Exact itself. Before the assistant put the change into "
                                      "Exact, Exact showed {value}. Check in Exact which value should go back, put it back yourself and tick "
                                      "the row off."),
    "hand_put_back_source:route": ("Exact zelf (gelezen voordat de assistent de wijziging erin zette)",
                                   "Exact itself (read before the assistant put the change in)"),
    # the review round of 17 November: the page and its texts
    "hand_tick_ended_exact": ("De assistent zette dit in Exact, maar de inkooporder die daarna binnenkwam toont {value}. Voer de wijziging in "
                              "Exact in, of vink de regel af als zij er al in staat.",
                              "The assistant put this into Exact, but the purchase order that came in afterwards shows {value}. Enter the "
                              "change in Exact, or tick the row off if it is already there."),
    "hand_tick_ended_unfound_exact": ("De assistent zette dit in Exact, maar in de inkooporder die daarna binnenkwam is deze regel niet terug "
                                      "te vinden. Kijk in Exact of de wijziging er staat en vink de regel dan af, of haal de regel van de lijst.",
                                      "The assistant put this into Exact, but this line cannot be found in the purchase order that came in "
                                      "afterwards. Check in Exact whether the change is there and tick the row off, or take the row off "
                                      "the list."),
    # the second review round of 17 November: the button writes no put-back, and the row and the note of Terugdraaien in Exact say
    # what the connection read in Exact when that is an empty field or nothing
    "hand_exact_put_back_empty_row": ("Dit terugzetten voert de assistent niet zelf in Exact in. Voordat de assistent de wijziging in Exact "
                                      "zette, was dit veld in Exact leeg. Kijk in Exact welke waarde terug moet, zet die zelf terug en vink "
                                      "de regel af.",
                                      "The assistant does not enter this put-back in Exact itself. Before the assistant put the change into "
                                      "Exact, this field in Exact was empty. Check in Exact which value should go back, put it back yourself "
                                      "and tick the row off."),
    "hand_exact_put_back_unread_row": ("Dit terugzetten voert de assistent niet zelf in Exact in. De koppeling heeft niet gelezen wat er in "
                                       "Exact stond voordat de assistent de wijziging erin zette. Kijk in Exact welke waarde terug moet, zet "
                                       "die zelf terug en vink de regel af.",
                                       "The assistant does not enter this put-back in Exact itself. The connection did not read what Exact "
                                       "showed before the assistant put the change in. Check in Exact which value should go back, put it back "
                                       "yourself and tick the row off."),
    "hand_put_back_other_route_empty": ("Let op: voordat de assistent de wijziging in Exact zette, was dit veld in Exact leeg. Kijk in Exact "
                                        "welke waarde terug moet.",
                                        "Note: before the assistant put the change into Exact, this field in Exact was empty. Check in Exact "
                                        "which value should go back."),
    "hand_put_back_other_route_unread": ("Let op: de koppeling heeft niet gelezen wat er in Exact stond voordat de assistent de wijziging erin "
                                         "zette. Kijk in Exact welke waarde terug moet.",
                                         "Note: the connection did not read what Exact showed before the assistant put the change in. Check in "
                                         "Exact which value should go back."),
}
PRICE_ROW = "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,70"
PRICE_ROW_EN = "Purchase order 250108 (S08), line 1 (KM-0801): price 12.50 → 12.70"
PRICE, ARTICLE = "(KM-0801): prijs 12,50", "artikel KM-0801"           # the words of MU08's two rows, whatever other rows the list holds


def outcome(kind: str, reason: str = "made_up", before: str | None = None):
    from aid.execution.exact_route import Outcome
    return Outcome(kind=kind, reason=reason, before=before)


class StandIn:
    """A made-up route into Exact for the tests, which writes nothing anywhere. It answers each call with the next outcome given
    (verified, with the change's old value read before, once none is left), keeps the change and the time of each call, and runs
    `during` in the middle of a call, so that a test stages what happens meanwhile: a tick in another browser tab, a newer row of
    the runner, a file of the marks that another program holds."""

    def __init__(self, *outcomes, target: str = "test", up: bool = True, during=None):
        self.outcomes, self.target, self.up, self.during = list(outcomes), target, up, during
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return self.up

    def write(self, change, now):
        self.calls.append((change, now))
        if self.during is not None:
            self.during(change)
        return self.outcomes.pop(0) if self.outcomes else outcome("verified", "read_back", change.old_value)


def route_page(tmp_path: Path, madeup_out: Path, route, cases=("MU08", "MU14"), share: bool = False, file: bool = True, **values) -> tuple:
    """The review page of test_hand_tab.tab_page with the route given to build_app, on a made-up settings file in the test mode (unless
    the values say otherwise) and a clock the test moves: (the page, the folder <cases_root>/prepared, the Deps, the clock). MU08
    prepares the price of PO 250108's line 1 and the article's price, MU14 a delivery date as a week."""
    path = settings_file(tmp_path, **({"test_mode": True} | values))
    settings = load_settings(path)
    prepared = settings.cases_root / "prepared"
    clock = Clock(datetime(2026, 11, 12, 9, 0))
    d = deps(tmp_path, madeup_out, writer=PreparedWriter(prepared)).model_copy(update={"settings": settings, "clock": clock})
    for minute, name in enumerate(cases):
        case = open_upload_case(settings.cases_root, [madeup_out / f"{name}_conf.pdf"], PO[name], "confirmation", datetime(2026, 11, 12, 9, minute))
        record_dispositions(case, run_case(case, d))
    return build_app(d, settings_file=path if file else None, share=share, route=route), prepared, d, clock


def pressed(page, ticked, language: str = "nl") -> dict:
    """What the button's handler gives for the rows ticked, by the names of the list's outputs."""
    return listed_by(page, WRITE, ticked, language)


def log_lines(prepared: Path) -> list[dict]:
    path = prepared / LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def mark_lines(prepared: Path) -> list[dict]:
    path = prepared / MARKS_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def texts_of(listed: dict) -> list[str]:
    return [text for text, _ in listed["rows"]["choices"]]


# --- the plug point ---

def test_the_plug_point_gives_no_route_and_reads_nothing():
    """route_for is the one place where a connector would be built once IT names the route. In this version it gives None and
    builds and reads nothing: not even an attribute of the settings it is given."""
    from aid.execution import exact_route

    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"route_for read {name}")

    assert exact_route.route_for(Untouchable()) is None and exact_route.route_for(SETTINGS) is None
    with_route = SETTINGS.model_copy(update={"test_mode": True})
    assert exact_route.route_for(with_route) is None


def test_an_outcome_has_one_of_five_kinds_and_a_fixed_reason():
    """An outcome is verified (written and read back), not_made (refused or not sent: nothing is in Exact), not_verified (accepted,
    the read-back differs), not_known (no clear answer) or handed_over (a route fed by a file took it and has not applied it yet).
    Its reason is a fixed key, never the text of an error, and `before` is the value read before the write, or none."""
    from pydantic import ValidationError

    from aid.execution.exact_route import KINDS, Outcome
    assert KINDS == ("verified", "not_made", "not_verified", "not_known", "handed_over")
    for kind in KINDS:
        assert Outcome(kind=kind, reason="made_up").before is None
    assert Outcome(kind="verified", reason="read_back", before="12.50").before == "12.50"
    for kind, reason in (("written", "made_up"), ("verified", "HTTP 500: Internal Server Error at https://exact.example.com/api"),
                         ("verified", "connection refused"), ("verified", ""), ("verified", "Read_back")):
        with pytest.raises(ValidationError):
            Outcome(kind=kind, reason=reason)


def test_a_route_is_what_the_protocol_names_and_its_docstring_says_what_a_connector_must_do():
    """A route has its target ("test" or "live", the administration it writes to), available() and write(change, now) -> Outcome.
    The module's docstring is the brief for whoever writes a connector, without naming the token of the earlier route, which no
    source outside the tests names (test_settings.py)."""
    from aid.execution import exact_route
    assert isinstance(StandIn(), exact_route.Route) and exact_route.TARGETS == ("test", "live")
    assert exact_route.LOG == LOG and exact_route.WRITTEN == "exact"
    described = " ".join((exact_route.__doc__ or "").split())
    for words in ("route_for(settings)", "builds and reads nothing", "reads the value before", "reads it back",
                  "never writes a value it did not mean to write", "the company's secret store", "a name of its own",
                  "A doubt that outlives its operation", "A read that lags behind the write", "never the Deps' writer",
                  "<cases_root>/prepared/exact_writes.jsonl"):
        assert words in described, words


def test_the_review_command_gives_build_app_the_route_of_the_plug_point(tmp_path, monkeypatch):
    """aid/review/__main__.py asks route_for(settings) and gives build_app its route, by keyword, when there is one; in this version
    there is none, and build_app is called with the options it had before (test_settings_tab.py pins them)."""
    import aid.review.__main__ as review
    built, asked = [], []

    class Page:
        def launch(self, **options):
            pass

        def block_thread(self):
            pass
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "temporary"))
    monkeypatch.setattr(review, "build_app", lambda d, **options: (built.append(options), Page())[1])
    monkeypatch.setattr(review, "launch_options", lambda page, share: {})
    path = settings_file(tmp_path)
    assert review.main(["--settings", str(path)]) == 0
    assert "route" not in built[-1]                          # none: build_app is called as before, its route None by default
    made = object()
    monkeypatch.setattr(review, "route_for", lambda settings: (asked.append(settings), made)[1])
    assert review.main(["--settings", str(path)]) == 0
    assert built[-1]["route"] is made and asked[0].cases_root == load_settings(path).cases_root


# --- the page without a route: as before, control for control ---

def test_without_a_route_the_tab_has_no_button_and_never_reads_the_routes_log(tmp_path, madeup_out, monkeypatch):
    """build_app's route is None by default, the plug point's own answer: the tab then has none of the button's parts, the intro of
    the tab as it was, and no read of the route's log, even when one lies in the folder with a write in doubt in it."""
    from aid.execution import exact_route
    from aid.tests.test_hand_tab import tab_page
    gr = import_gradio()
    page, prepared, d = tab_page(tmp_path, madeup_out)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(price)]
    exact_route.log_attempt(prepared, change, "test", datetime(2026, 11, 12, 9, 30))       # a write in doubt, as a page with a route left it

    def unread(*args, **kwargs):
        raise AssertionError("the page without a route read the route's log")
    monkeypatch.setattr(exact_route, "records", unread)
    monkeypatch.setattr(exact_route, "doubts", unread)
    listed = listed_by(page, "list_opened", "nl")
    assert listed["intro"] == NL["hand_intro"] and PRICE_ROW in texts_of(listed)          # no note on the row
    handler(page, "hand_relabel")("en", [price], None)
    names = {getattr(f.fn, "__name__", "") for f in page.fns.values()}
    assert WRITE not in names
    buttons = [block.value for block in inside(hand_tab(page)) if isinstance(block, gr.Button)]
    assert NL["hand_exact"] not in buttons and len(buttons) == len(set(buttons)) == 7


# --- the page with a route: the button and its press ---

def test_with_a_route_the_button_stands_first_above_the_list_and_is_wired_as_the_other_presses(tmp_path, madeup_out):
    """The button stands first in the row of buttons above the list, the tab's one filled button; the two buttons for what the buyer
    types themselves stay beside it. Its press takes the rows ticked and the language and gives the list as every press of the tab
    gives it, followed by the same reads (the handled changes, the price lists, the copy buttons) and clearing the line of new
    changes. The language switch relabels it through the tab's own relabel, as its last output, so that no listener is added to the
    switch and the outputs before it keep their places; nothing of it is on the page's timer."""
    gr = import_gradio()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, StandIn())
    from aid.tests.test_hand_tab import tab_page
    plain = tab_page(tmp_path / "plain", madeup_out)[0]
    tab = hand_tab(page)
    [actions] = [block for block in inside(tab) if getattr(block, "elem_id", None) == "hand-actions"]
    pair = actions.children[0]
    assert [(button.value, button.variant) for button in pair.children] == [(NL["hand_exact"], "primary"), (NL["hand_entered"], "secondary"),
                                                                           (NL["hand_dropped"], "secondary")]
    assert [button for button in inside(tab) if isinstance(button, gr.Button) and button.variant == "primary"] == pair.children[:1]
    language = [block for block in page.blocks.values() if isinstance(block, gr.Radio) and block.label == NL["language"]][0]
    [fill] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "list_opened"]
    [press] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == WRITE]
    rows = [block for block in inside(tab) if isinstance(block, gr.CheckboxGroup)][0]
    assert press.targets == [(pair.children[0]._id, "click")] and press.inputs == [rows, language] and press.outputs == fill.outputs
    key = next(key for key, f in page.fns.items() if f is press)
    after = {getattr(f.fn, "__name__", "") for f in page.fns.values() if f.trigger_after == key}
    assert after == {"done_opened", "pricelists_opened", "copies_opened"}
    [cleared] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "hand_new_cleared"]
    assert (pair.children[0]._id, "click") in cleared.targets and len(cleared.targets) == 10
    [relabel] = [f for f in page.fns.values() if getattr(f.fn, "__name__", "") == "hand_relabel"]
    [plain_relabel] = [f for f in plain.fns.values() if getattr(f.fn, "__name__", "") == "hand_relabel"]
    assert relabel.outputs[-1] is pair.children[0] and len(relabel.outputs) == len(plain_relabel.outputs) + 1
    english = relabel.fn("en", [], None)
    assert len(english) == len(relabel.outputs) and english[-1] == {"__type__": "update", "value": EN["hand_exact"]}
    assert english[6:10] == plain_relabel.fn("en", [], None)[6:10]

    def on_switch(one) -> list[str]:
        one_language = [block for block in one.blocks.values() if isinstance(block, gr.Radio) and block.label == NL["language"]][0]
        return sorted(getattr(f.fn, "__name__", "") for f in one.fns.values() if (one_language._id, "change") in f.targets)
    assert on_switch(page) == on_switch(plain)

    def on_timer(one) -> list[str]:
        [timer] = [block for block in one.blocks.values() if isinstance(block, gr.Timer)]
        return sorted(getattr(f.fn, "__name__", "") for f in one.fns.values() if (timer._id, "tick") in f.targets)
    assert on_timer(page) == on_timer(plain)
    listed = listed_by(page, "list_opened", "nl")
    assert listed["intro"] == f"{NL['hand_intro_route']}\n\n{NL['hand_test_mode']}"
    assert listed_by(page, "list_opened", "en")["intro"].startswith(EN["hand_intro_route"])


def test_a_verified_write_marks_the_change_entered_by_exact_and_it_leaves_the_list(tmp_path, madeup_out):
    """The press writes the change that holds the ticked row's entry ids now, through the route; after a verified write it marks the
    change entered with by "exact", the row leaves the list and the handled changes name it as put into Exact by the assistant. The
    route's log holds the attempt before the call and the outcome after it, each naming the entry ids, the kind, the order, the
    line, the article, the old and the new value, the outcome's kind and reason and the value read before."""
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    listed = listed_by(page, "list_opened", "nl")
    price = value_of(listed, PRICE)
    clock.later()
    done = pressed(page, [price])
    assert done["note"] == NL["hand_exact_written_one"] == "1 wijziging in Exact gezet."
    assert done["tab"]["label"] == "In Exact invoeren (2)" and PRICE_ROW not in texts_of(done) and done["rows"]["value"] == []
    [(change, now)] = route.calls
    assert change.ids == ids_of(price) and (change.kind, change.po_number, change.line_id, change.old_value, change.new_value) == (
        "po_line_price", "250108", "1", "12.50", "12.70") and now == datetime(2026, 11, 12, 9, 1)
    assert [(line["entry_id"], line["mark"], line["at"], line["by"]) for line in mark_lines(prepared)] == [
        (entry_id, "entered", "2026-11-12T09:01:00", "exact") for entry_id in ids_of(price)]
    attempt, result = log_lines(prepared)
    named = {"entry_ids": ids_of(price), "kind": "po_line_price", "po_number": "250108", "version": 1, "line_id": "1", "article_code": None,
             "old_value": "12.50", "new_value": "12.70", "target": "test"}
    assert attempt == {"at": "2026-11-12T09:01:00", **named, "outcome": None, "reason": None, "before": None}
    assert result == {"at": "2026-11-12T09:01:00", **named, "outcome": "verified", "reason": "read_back", "before": "12.50"}
    sections = done_of(page, "done_opened", "nl", None)
    assert f"- {PRICE_ROW} (door de assistent in Exact gezet op 12-11-2026)" in sections["done"].splitlines()
    assert "(put into Exact by the assistant on 12-11-2026)" in done_of(page, "done_opened", "en", None)["done"]
    # in English, with two rows at once: the price of the article as well, through the same press
    other_route = StandIn()
    other, other_prepared, _, _ = route_page(tmp_path / "en", madeup_out, other_route)
    listed = listed_by(other, "list_opened", "en")
    both = [value_of(listed, "price 12.50"), value_of(listed, "purchase price")]
    assert pressed(other, both, "en")["note"] == "2 changes put into Exact." and len(other_route.calls) == 2
    assert [change.kind for change in open_changes(other_prepared)] == ["po_line_date"]


def test_the_attempt_is_in_the_routes_log_before_the_route_is_called(tmp_path, madeup_out):
    seen = []
    route = StandIn(during=lambda change: seen.append(log_lines(change_folder[0])))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    change_folder = [prepared]
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    pressed(page, [price])
    [lines] = seen
    assert [(line["entry_ids"], line["outcome"]) for line in lines] == [(ids_of(price), None)]     # the attempt alone, while the call runs
    assert [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]


def test_a_change_not_made_stays_on_the_list_and_can_be_pressed_again(tmp_path, madeup_out):
    route = StandIn(outcome("not_made", "refused"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    first = pressed(page, [price])
    assert first["note"] == NL["hand_exact_not_made_one"] and PRICE_ROW in texts_of(first) and first["rows"]["value"] == [price]
    assert mark_lines(prepared) == [] and first["tab"]["label"] == "In Exact invoeren (3)"
    assert PRICE_ROW in texts_of(listed_by(page, "list_opened", "nl"))                       # no note on the row: nothing is in Exact
    again = pressed(page, [price], "en")
    assert again["note"] == "1 change put into Exact." and len(route.calls) == 2
    assert [line["outcome"] for line in log_lines(prepared)] == [None, "not_made", None, "verified"]


@pytest.mark.parametrize("kind, row_note", [("not_verified", "hand_exact_unsure_row"), ("not_known", "hand_exact_unsure_row"),
                                            ("handed_over", "hand_exact_handed_over_row")])
def test_a_write_in_doubt_stays_with_a_note_and_a_later_press_does_not_write_it_again(tmp_path, madeup_out, kind, row_note):
    """not_verified, not_known and handed_over write no mark: the row stays, with a note to look in Exact, and a later press passes it
    over without calling the route, until the buyer ticks it (taken back afterwards, it may be written again)."""
    route = StandIn(outcome(kind, "made_up"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    first = pressed(page, [price])
    said = {"not_verified": "hand_exact_unsure_one", "not_known": "hand_exact_unsure_one", "handed_over": "hand_exact_handed_over_one"}[kind]
    assert first["note"] == NL[said] and mark_lines(prepared) == []
    assert f"{PRICE_ROW}. {NL[row_note]}" in texts_of(first)
    assert f"{PRICE_ROW_EN}. {EN[row_note]}" in texts_of(listed_by(page, "list_opened", "en"))
    clock.later()
    again = pressed(page, [price])
    assert again["note"] == NL["hand_exact_passed_one"] and len(route.calls) == 1 and again["rows"]["value"] == []     # P10: in doubt
    # the buyer looks in Exact and ticks it; taken back later, the row is open again and the press writes it
    clock.later()
    listed_by(page, "changes_entered", [price], "nl")
    clock.later()
    assert done_of(page, "change_reopened", price, "nl")["done_note"] == NL["hand_reopened"]
    assert PRICE_ROW in texts_of(listed_by(page, "list_opened", "nl"))                     # the note went with the tick
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"] and len(route.calls) == 2


def test_a_print_that_shows_the_value_settles_a_write_in_doubt(tmp_path, madeup_out):
    """A newer print of the order that shows the value marks the change seen, as for any open row; once a later print ended that
    again, the row is open and the press may write it."""
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    pressed(page, [price])
    clock.later()
    mark(prepared, ids_of(price), "seen", clock(), by="version 2")
    clock.later()
    mark(prepared, ids_of(price), "reopened", clock(), by="version 3")
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"] and len(route.calls) == 2


@pytest.mark.parametrize("kind", ["not_made", "not_verified", "not_known", "handed_over"])
def test_an_attempt_without_its_outcome_reads_as_not_sure(tmp_path, madeup_out, monkeypatch, kind):
    """An attempt whose outcome never reached the log (the page was stopped during the call, or the line could not be written) is a
    write in doubt, whatever the route answered (R5 of the second review round): the press counts the row as not sure and clears
    its tick, the row says so, and a later press passes it over."""
    from aid.execution import exact_route
    route = StandIn(outcome(kind, "made_up"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds the log")
    monkeypatch.setattr(exact_route, "log_outcome", held)
    clock.later()
    first = pressed(page, [price])
    monkeypatch.undo()
    assert first["note"] == NL["hand_exact_unsure_one"] and first["rows"]["value"] == []
    assert [line["outcome"] for line in log_lines(prepared)] == [None] and mark_lines(prepared) == []
    clock.later()
    assert f"{PRICE_ROW}. {NL['hand_exact_unsure_row']}" in texts_of(listed_by(page, "list_opened", "nl"))
    clock.later()
    lines = log_lines(prepared)
    assert pressed(page, [price])["note"] == NL["hand_exact_passed_one"] and len(route.calls) == 1 and log_lines(prepared) == lines


class Broken(StandIn):
    """A made-up connector that breaks its contract: available() raises, write() raises, or write() gives no Outcome."""

    def __init__(self, how: str):
        super().__init__()
        self.how = how

    def available(self) -> bool:
        if self.how == "available":
            raise RuntimeError("made-up: https://exact.example.com/api refused the token")
        return True

    def write(self, change, now):
        self.calls.append((change, now))
        if self.how == "raises":
            raise RuntimeError("made-up: https://exact.example.com/api answered 500")
        return {"kind": "verified"}


def test_a_route_that_raises_or_gives_no_outcome_leaves_the_write_in_doubt(tmp_path, madeup_out):
    """available() that raises counts as a route that is not available, and nothing is sent; a write that raises, or that gives no
    Outcome, is not_known with a fixed reason of the page's own, so that the row is a write in doubt; the error's text is kept nowhere."""
    for how, note, reason in (("available", f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:unavailable']}", None),
                              ("raises", NL["hand_exact_unsure_one"], "raised"), ("no_outcome", NL["hand_exact_unsure_one"], "no_outcome")):
        route = Broken(how)
        page, prepared, d, clock = route_page(tmp_path / how, madeup_out, route, cases=("MU08",))
        price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
        assert pressed(page, [price])["note"] == note, how
        assert [line["reason"] for line in log_lines(prepared) if line["outcome"] is not None] == ([] if reason is None else [reason]), how
        assert mark_lines(prepared) == [] and (len(route.calls) == (0 if how == "available" else 1)), how
        assert "example.com" not in ((prepared / LOG).read_text(encoding="utf-8") if (prepared / LOG).exists() else ""), how


def week_range_unknown_and_empty(page, prepared: Path, d) -> dict[str, str]:
    """Rows whose new value is no single value: MU14's week, a range of days, a value the aid does not know ("onbekend") and a field
    that a put-back empties ("leeg"): {what: the row's value}."""
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    writer = PreparedWriter(prepared)
    writer.apply(WriteOp(kind="po_line_date", po_number="250108", version=1, line_id="1", old_value="2026-11-16", new_value="2026-11-23/2026-11-27"),
                 case_id, "R02", {}, datetime(2026, 11, 12, 9, 30))
    writer.apply(WriteOp(kind="po_line_quantity", po_number="250108", version=1, line_id="1", old_value="50", new_value=None), case_id, "undo",
                 {}, datetime(2026, 11, 12, 9, 30))
    made = WriteLogEntry(entry_id="0" * 31 + "1", case_id=case_id, status="done", previous="", new="60", rule_id="B01", evidence={"read_before": ""},
                         op=WriteOp(kind="po_line_quantity", po_number="250108", version=1, line_id="2", old_value="50", new_value="60"),
                         timestamp="2026-11-12T09:30:00")
    WriteLog(d.settings.cases_root / case_id).append(made)
    handler(page, "do_undo")(case_id, made.entry_id, "nl")
    listed = listed_by(page, "list_opened", "nl")
    return {"week": value_of(listed, "week 48"), "range": value_of(listed, "23-11-2026/27-11-2026"), "unknown": value_of(listed, "aantal 50 → onbekend"),
            "empty": value_of(listed, "→ leeg")}


def test_the_press_passes_over_rows_it_cannot_write(tmp_path, madeup_out):
    """A row whose new value is no single number or day (a week, a range, "onbekend", "leeg"), a row of a try-out document, and a row
    that is no longer open as the buyer saw it (another browser tab ticked it, or a newer document replaced its value) are passed
    over: the route is not called, nothing is logged and nothing is marked."""
    from aid.tryout import confirmation_pdf, made_from
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    odd = week_range_unknown_and_empty(page, prepared, d)
    made = tmp_path / "proef.pdf"
    made.write_bytes(confirmation_pdf(made_from(d.store, "250101"), "price_small", datetime(2026, 11, 12, 9, 30)))
    case = open_upload_case(d.settings.cases_root, [made], "250101", "confirmation", datetime(2026, 11, 12, 9, 30))
    record_dispositions(case, run_case(case, d))
    listed = listed_by(page, "list_opened", "nl")
    tryouts = [value for text, value in listed["rows"]["choices"] if text.endswith(" (proefdocument)")]
    assert tryouts
    marks_before = mark_lines(prepared)
    for language in ("nl", "en"):
        for value in [*odd.values(), *tryouts]:
            result = pressed(page, [value], language)
            assert result["note"] == STRINGS[language]["hand_exact_passed_one"], value
            assert result["rows"]["value"] == [value]                                       # still ticked, for the buyer to handle
    several = pressed(page, list(odd.values()))
    assert several["note"] == NL["hand_exact_passed_many"].format(n=4)
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == marks_before
    # a row that another browser tab ticked meanwhile, and one whose value a newer document replaced
    price, article = value_of(listed, PRICE), value_of(listed, ARTICLE)
    mark(prepared, ids_of(article), "entered", clock())
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(price)]
    PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.90"),
                                   change.case_ids[0], "R04", {}, datetime(2026, 11, 12, 9, 40))
    gone = pressed(page, [price, article])
    assert gone["note"] == NL["hand_exact_passed_many"].format(n=2) and route.calls == [] and log_lines(prepared) == []
    assert gone["rows"]["value"] == []                                                      # neither row is listed any more


def test_a_row_another_case_joined_since_the_list_was_read_is_passed_over(tmp_path, madeup_out):
    """The change must still hold the entry ids the buyer saw: a second confirmation that asked the same price meanwhile made it one
    change of two rows, which the list shows as a new row."""
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    again = open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", datetime(2026, 11, 12, 9, 30))
    record_dispositions(again, run_case(again, d))
    result = pressed(page, [price])
    assert result["note"] == NL["hand_exact_passed_one"] and route.calls == []
    joined = value_of(result, PRICE)
    assert len(ids_of(joined)) == 2 and pressed(page, [joined])["note"] == NL["hand_exact_written_one"]


@pytest.mark.parametrize("cause", ["test_mode", "live", "unavailable", "settings"])
def test_the_press_writes_nothing_outside_the_test_mode_into_the_live_exact_without_a_connection_or_settings(tmp_path, madeup_out, cause):
    """During the BEP nothing is written into the live Exact: the press writes only while the test mode is on, the route's target is
    "test", the route is available and the settings file can be read at the moment of the press, as a close sends only then. Else
    nothing is sent or logged, the rows stay ticked, and the note says why."""
    route = StandIn(target="live" if cause == "live" else "test", up=cause != "unavailable")
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, test_mode=cause != "test_mode")
    listed = listed_by(page, "list_opened", "nl")
    ticked = [value_of(listed, PRICE), value_of(listed, ARTICLE)]
    if cause == "settings":
        path = tmp_path / "settings.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n{", encoding="utf-8")       # the file no longer reads
    for language in ("nl", "en"):
        strings = STRINGS[language]
        result = pressed(page, ticked, language)
        assert result["note"] == f"{strings['hand_exact_not_made_many'].format(n=2)} {strings[f'hand_exact_stop:{cause}']}"
        assert result["rows"]["value"] == ticked and result["tab"]["label"] == strings["hand_tab_count"].format(n=3)
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []


def test_a_routes_log_that_cannot_be_written_sends_nothing(tmp_path, madeup_out, monkeypatch):
    """The route's log can be neither read nor written (another program holds it). The failure is forced by patching (R13 of the
    second review round), not by a folder in its place, whose error differs from one system to another: nothing is sent or marked,
    the row stays ticked, and the list itself still reads."""
    from aid.execution import exact_route
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds the route's log")
    monkeypatch.setattr(exact_route, "records", held)
    monkeypatch.setattr(exact_route, "_append_line", held)
    clock.later()
    result = pressed(page, [price])
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:log']}"
    assert route.calls == [] and mark_lines(prepared) == [] and result["rows"]["value"] == [price] and not (prepared / LOG).exists()
    assert PRICE_ROW in texts_of(listed_by(page, "list_opened", "nl"))         # the list itself still reads


def test_a_mark_refused_after_a_verified_write_says_the_value_is_in_exact(tmp_path, madeup_out):
    """The route wrote and read the value back, but the mark writes nothing: another browser tab ticked the row meanwhile, or the
    runner put a newer value on the list. The note says that the value is in Exact and the row was not ticked off; for a newer value,
    that the newer row asks another value (S4)."""
    def ticked_elsewhere(change):
        mark(change_folder[0], change.ids, "entered", datetime(2026, 11, 12, 9, 0, 30))
    route = StandIn(during=ticked_elsewhere)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    change_folder = [prepared]
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    assert pressed(page, [price])["note"] == NL["hand_exact_unmarked_one"]
    assert [line["by"] for line in mark_lines(prepared)] == ["buyer"]
    assert [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]

    def replaced(change):
        PreparedWriter(change_folder[0]).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50",
                                                       new_value="12.90"), change.case_ids[0], "R04", {}, datetime(2026, 11, 12, 9, 40))
    other, other_prepared, _, _ = route_page(tmp_path / "replaced", madeup_out, StandIn(during=replaced))
    change_folder = [other_prepared]
    result = pressed(other, [value_of(listed_by(other, "list_opened", "en"), "price 12.50")], "en")
    assert result["note"] == EN["hand_exact_replaced_one"] and mark_lines(other_prepared) == []        # S4: the newer row asks another value
    assert "Purchase order 250108 (S08), line 1 (KM-0801): price 12.50 → 12.90" in texts_of(result)


def test_a_mark_refused_because_the_marks_are_held_keeps_its_note(tmp_path, madeup_out, monkeypatch):
    """The file of the marks is held by another program from the moment of the call on, at the mark and at the read of the list
    after the press: the value is in Exact, the row is not ticked off, and the note says so, before the line that the list cannot
    be read now. The failure is forced by patching, with an error of the test's own (R13 of the second review round), so that the
    note does not rest on what one system or another says of a file it cannot open."""
    from aid.review import page as page_module

    class MarksHeld(PermissionError):
        """made-up: another program holds marks.jsonl"""

    def held(*args, **kwargs):
        raise MarksHeld("made-up: another program holds marks.jsonl")

    def held_from_now_on(change):
        monkeypatch.setattr(page_module, "mark", held)
        monkeypatch.setattr(page_module, "changes", held)
    route = StandIn(during=held_from_now_on)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert result["note"] == f"{NL['hand_exact_unmarked_one']} {NL['settings_failed'].format(code='MarksHeld')}"
    assert result["tab"]["label"] == NL["hand_tab_unread"] and len(route.calls) == 1 and mark_lines(prepared) == []
    assert [result[part] for part in ("intro", "rows", "block", "seen")] == [{"__type__": "update"}] * 4


def test_the_buyers_other_ticks_are_kept(tmp_path, madeup_out):
    """After a press the rows that were put into Exact leave the list; the other rows the buyer ticked stay ticked, for them to handle:
    a week passed over, an article price not made."""
    route = StandIn(outcome("verified", "read_back", "12.50"), outcome("not_made", "refused"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    listed = listed_by(page, "list_opened", "nl")
    price, date, article = (value_of(listed, words) for words in (PRICE, "leverdatum", ARTICLE))
    result = pressed(page, [price, date, article])
    assert result["note"] == " ".join([NL["hand_exact_written_one"], NL["hand_exact_not_made_one"], NL["hand_exact_passed_one"]])
    assert result["rows"]["value"] == [date, article] and [change.ids for change, _ in route.calls] == [ids_of(price), ids_of(article)]
    nothing = pressed(page, [])
    assert nothing["note"] == NL["hand_tick_first"] and len(route.calls) == 2


def test_weer_op_de_lijst_zetten_refuses_a_change_the_aid_put_into_exact_and_terugdraaien_still_prepares(tmp_path, madeup_out):
    """A change the aid put into Exact is not put on the list again: the press says to choose "Terugdraaien in Exact", which prepares
    the old value as a row, as for a tick, and calls no route. Since the second review round (R1) the button does not put that row
    into Exact: it passes it over, and the buyer enters it."""
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    pressed(page, [price])
    before = (prepared / MARKS_FILE).read_bytes()
    chosen = chosen_of(done_of(page, "done_opened", "nl", None), PRICE)
    for language in ("nl", "en"):
        refused = done_of(page, "change_reopened", chosen, language)
        assert refused["done_note"] == STRINGS[language]["hand_reopen_written"]
    assert (prepared / MARKS_FILE).read_bytes() == before
    clock.later()
    put = done_of(page, "change_put_back", chosen, "nl")
    back = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
    assert put["done_note"] == NL["hand_put_back_listed"].format(row=back) and len(route.calls) == 1
    clock.later()
    lines, value = log_lines(prepared), value_of(put, "Terugzetten")
    assert pressed(page, [value])["note"] == NL["hand_exact_passed_one"] and len(route.calls) == 1 and log_lines(prepared) == lines
    assert f"{back}. {nl_en('hand_exact_put_back_hand_row')[0]}" in texts_of(put)          # the row's note, before any press
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(value)]
    assert (change.rule_id, change.old_value, change.new_value, change.puts_back) == ("undo", "12.70", "12.50", ids_of(price)[0])
    # P5: a put-back marked entered by "exact" is not put on the list again either, and the note does not point to Terugdraaien in
    # Exact, which cannot put a put-back back: the old value is the buyer's to put back. Since R1 the button marks no put-back, so
    # the mark is staged here as it would read.
    clock.later()
    mark(prepared, ids_of(value), "entered", clock(), by="exact")
    clock.later()
    written_back = chosen_of(done_of(page, "done_opened", "nl", None), "Terugzetten")
    before = (prepared / MARKS_FILE).read_bytes()
    for language in ("nl", "en"):
        assert done_of(page, "change_reopened", written_back, language)["done_note"] == STRINGS[language]["hand_put_back_cannot"]
        assert done_of(page, "change_put_back", written_back, language)["done_note"] == STRINGS[language]["hand_put_back_cannot"]
    assert (prepared / MARKS_FILE).read_bytes() == before


def test_on_a_share_link_or_without_a_settings_file_a_route_offers_nothing(tmp_path, madeup_out):
    route = StandIn()
    for share, file, key in ((True, True, "settings_share"), (False, False, "settings_no_file")):
        page = route_page(tmp_path / key, madeup_out, route, cases=("MU08",), share=share, file=file)[0]
        tab = hand_tab(page)
        assert [type(child).__name__ for child in tab.children] == ["Markdown"] and tab.children[0].value == NL[key]
        assert WRITE not in {getattr(f.fn, "__name__", "") for f in page.fns.values()}
    assert route.calls == []


# --- the list as the tab shows it with a route (aid/review/hand_list.py) ---

def test_the_list_shows_the_intro_of_the_button_and_the_handled_change_the_aid_put_into_exact(tmp_path, madeup_out):
    from aid.review import hand_list
    page, prepared, d, clock = route_page(tmp_path, madeup_out, StandIn(), cases=("MU08",))
    root = d.settings.cases_root

    def rows_of(doubted=None, language="nl") -> list[str]:
        return [text for _, _, rows in hand_list.blocks(open_changes(prepared), d.store, root, language, doubted) for text, _ in rows]
    listed = hand_list.blocks(open_changes(prepared), d.store, root, "nl")
    assert hand_list.intro(listed, 0, "nl", routed=True) == NL["hand_intro_route"]
    assert hand_list.intro(listed, 0, "nl") == NL["hand_intro"]                              # without a route, as before
    [price, article] = sorted(open_changes(prepared), key=lambda change: change.kind != "po_line_price")
    plain = rows_of()
    assert rows_of({}) == plain and rows_of({"no such entry": "not_known"}) == plain
    for kind, key in (("not_verified", "hand_exact_unsure_row"), ("not_known", "hand_exact_unsure_row"), ("handed_over", "hand_exact_handed_over_row")):
        for language in ("nl", "en"):
            row, other = (rows_of(None, language)[index] for index in (0, 1))
            assert rows_of({price.ids[0]: kind}, language) == [f"{row}. {STRINGS[language][key]}", other], (kind, language)
    mark(prepared, price.ids, "entered", datetime(2026, 11, 12, 9, 5), by="exact")
    [(text, _)] = [row for row in hand_list.done_rows(changes(prepared)[0], datetime(2026, 11, 12, 10, 0), "nl") if "prijs" in row[0]]
    assert text.endswith("(door de assistent in Exact gezet op 12-11-2026)")
    [(text, _)] = [row for row in hand_list.done_rows(changes(prepared)[0], datetime(2026, 11, 12, 10, 0), "en") if "price" in row[0]]
    assert text.endswith("(put into Exact by the assistant on 12-11-2026)")


def test_the_rule_of_one_value_is_the_rule_of_the_copy_buttons(tmp_path, madeup_out):
    """The press writes a new value only when the copy buttons offer it: one number, or one day."""
    from aid.review import hand_list
    page, prepared, d, clock = route_page(tmp_path, madeup_out, StandIn())
    week_range_unknown_and_empty(page, prepared, d)
    for change in open_changes(prepared):
        offered = any(part == "value" for part, _ in hand_list.copy_parts(change, d.settings.cases_root, "nl"))
        assert hand_list.one_value(change) is offered, change.new_value


# --- the texts and the documents ---

def test_the_texts_of_the_button_in_both_languages():
    assert {key: (NL[key], EN[key]) for key in TEXTS} == TEXTS
    for strings in (NL, EN):
        assert not [key for key in TEXTS if re.search(r"\bR\d{2}\b|\u2014|\u2013", strings[key])]
    assert TEXTS["hand_exact"][0] == "Aangevinkte regels in Exact zetten" and TEXTS["hand_done_written"][0].startswith("(door de assistent in Exact gezet op")


def test_the_documents_say_what_the_button_does_once_a_connection_exists_and_that_it_does_not_show_until_then():
    manual = manual_sections()
    two_six = " ".join(manual["2.6"].split())
    for key in ("hand_exact", "hand_intro_route", "hand_exact_written_one", "hand_exact_not_made_one", "hand_exact_unsure_one",
                "hand_exact_handed_over_one", "hand_exact_passed_one", "hand_exact_unmarked_one", "hand_exact_unsure_row",
                "hand_exact_handed_over_row", "hand_reopen_written", "hand_exact_stop:test_mode", "hand_exact_stop:live"):
        assert f"\"{NL[key]}\"" in two_six, key
    assert "\"(door de assistent in Exact gezet op 18-11-2026)\"" in two_six
    assert "does not show until a connection to Exact exists" in two_six and "**The button for Exact.**" in two_six
    three_ten = " ".join(manual["3.10"].split())
    for words in ("`route_for(settings)` of `aid/execution/exact_route.py`", "returns `None`", "`cases/prepared/exact_writes.jsonl`",
                  "`\"by\": \"exact\"`", "`verified`", "`not_made`", "`not_verified`", "`not_known`", "`handed_over`",
                  "never the Deps' writer", "not `AID_EXACT_TOKEN`", "**The plug point of the button.**"):
        assert words in three_ten, words
    guide = " ".join(manual["7"].split())
    assert ("Zodra er een koppeling met Exact is, staat boven de lijst ook de knop \"Aangevinkte regels in Exact zetten\"" in guide
            and "Zolang die koppeling er niet is, staat die knop er niet." in guide)
    readme = " ".join(Path("aid/README.md").read_text(encoding="utf-8").split())
    assert ("`route_for` of `aid/execution/exact_route.py` is the one place where a connection would be built, and it builds none, so the "
            "tab \"In Exact invoeren\" has no button that puts a change into Exact") in readme
    assert "exact_route.py: the plug point of a route into Exact for the button of the tab \"In Exact invoeren\", which gives none" in readme
    from aid.review import page as page_module
    described = " ".join(page_module.__doc__.split())
    assert "\"Aangevinkte regels in Exact zetten\"" in described and "only when build_app is given a route" in described


# --- the review round of 17 November: the safety items (the triage's S1 to S10) ---

ROW_BACK = "Inkooporder 250108 (S08), Terugzetten: regel 1 (KM-0801): prijs 12,70 → 12,50"
ROW_BACK_EN = "Purchase order 250108 (S08), Put back: line 1 (KM-0801): price 12.70 → 12.50"


def nl_en(key: str) -> tuple[str, str]:
    """The literal copies of a key in Dutch and in English."""
    return TEXTS[key]


def tryout_rows(tmp_path: Path, page, d) -> tuple[str, list[str]]:
    """A try-out of PO 250101 (price_small), run as the runner runs it: (its case id, the values of its two rows on the list, the
    line's price and the article's price, each "(proefdocument)" there)."""
    from aid.tryout import confirmation_pdf, made_from
    made = tmp_path / "proef.pdf"
    made.write_bytes(confirmation_pdf(made_from(d.store, "250101"), "price_small", datetime(2026, 11, 12, 9, 30)))
    case = open_upload_case(d.settings.cases_root, [made], "250101", "confirmation", datetime(2026, 11, 12, 9, 30))
    record_dispositions(case, run_case(case, d))
    listed = listed_by(page, "list_opened", "nl")
    return case.case_id, [value for text, value in listed["rows"]["choices"] if text.endswith(" (proefdocument)")]


def ended_by_print(prepared: Path, ids: list[str], now: datetime, shown: str | None, by: str = "version 2") -> None:
    """A newer print that ends the tick of the entries, as contradict() of aid/execution/by_hand.py writes it: the mark "reopened" by
    the print, with the value it showed for the line (made-up data, appended in that format)."""
    with open(prepared / MARKS_FILE, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps({"entry_id": entry_id, "mark": "reopened", "at": now.isoformat(), "by": by, "shown": shown}) + "\n"
                        for entry_id in ids))


@pytest.mark.parametrize("how", ["held", "broken", "gone"])
def test_a_try_out_whose_case_cannot_be_read_at_the_press_is_passed_over(tmp_path, madeup_out, monkeypatch, how):
    """S1: the press judges the try-out itself, at the moment of the press and fail-closed, as a close judges it before it sends
    (mark_for_sending): a case whose case.json is held (another program has it open) or broken, or whose folder is gone, may be a
    try-out's, so its rows are passed over, though the list, which errs the other way, no longer marks them "(proefdocument)".
    Nothing is sent, logged or marked."""
    from aid import tryout_mark
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    case_id, tryouts = tryout_rows(tmp_path, page, d)
    assert len(tryouts) == 2
    marks_before = mark_lines(prepared)
    monkeypatch.setattr(tryout_mark, "READ_PAUSE", 0)
    if how == "held":
        def held(*args, **kwargs):
            raise PermissionError("made-up: another program holds case.json")
        monkeypatch.setattr(tryout_mark, "load_case", held)
    elif how == "broken":
        (d.settings.cases_root / case_id / "case.json").write_text("{", encoding="utf-8")
    else:
        shutil.rmtree(d.settings.cases_root / case_id)
    clock.later()
    result = pressed(page, tryouts)
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == marks_before
    assert result["note"] == NL["hand_exact_passed_many"].format(n=2)


def test_the_press_reads_a_try_out_cart_page_as_a_try_out_and_a_suppliers_document_as_none(tmp_path, madeup_out):
    """S1: the press judges a case by its document as is_tryout tells a try-out, a try-out confirmation and a try-out cart page
    alike, as mark_for_sending does for a close. A supplier's document read now bears no mark, and a case that is not there
    cannot be judged."""
    from datetime import date

    from aid.case import open_case
    from aid.tryout import cart_html, made_from
    from aid.tryout_mark import mark_for_writing
    d = deps(tmp_path, madeup_out)
    root = tmp_path / "cases"
    cart = tmp_path / "proefwinkel.html"
    cart.write_text(cart_html(made_from(d.store, "250101"), "price_small", date(2026, 11, 12)), encoding="utf-8")
    tryout = open_case(root, "cart", [cart], "250101", datetime(2026, 11, 12, 9, 30))
    supplier = open_case(root, "confirmation", [madeup_out / "MU08_conf.pdf"], "250108", datetime(2026, 11, 12, 9, 31))
    assert mark_for_writing(root, tryout.case_id) is True and mark_for_writing(root, supplier.case_id) is False
    assert mark_for_writing(root, "250199_20261112T093200") is None


def test_a_price_lists_article_price_is_no_try_out_and_is_put_into_exact(tmp_path, madeup_out):
    """S1: a price list is told from a case by the case id the price-list import gives it (list_of of aid/pricelist.py), not by a
    folder: it has none, and its article prices are still put into Exact."""
    from aid.pricelist import import_price_list
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    made = tmp_path / "prijslijst.csv"
    made.write_text("item_code,price\nKM-9001,4.20\n", encoding="utf-8")
    assert len(import_price_list(made, "S01", {"KM-9001": "4.00"}, PreparedWriter(prepared), datetime(2026, 11, 12, 9, 30)).updated) == 1
    value = value_of(listed_by(page, "list_opened", "nl"), "KM-9001")
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(value)]
    assert change.case_ids == ["pricelist_S01_20261112T093000"] and not (d.settings.cases_root / change.case_ids[0]).exists()
    clock.later()
    assert pressed(page, [value])["note"] == NL["hand_exact_written_one"]
    [(sent, _)] = route.calls
    assert (sent.kind, sent.article_code, sent.new_value) == ("article_price", "KM-9001", "4.20")


def written_and_put_back(tmp_path: Path, madeup_out: Path, before: str | None, by_hand: bool = False) -> tuple:
    """MU08's price row put into Exact through a stand-in route that read `before` there (or, by_hand, ticked by the buyer), and then
    "Terugdraaien in Exact" on it: (the page, the folder of the list, the route, the clock, the value of the put-back row, the note of
    Terugdraaien)."""
    route = StandIn(outcome("verified", "read_back", before))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    if by_hand:
        listed_by(page, "changes_entered", [price], "nl")
    else:
        assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    clock.later()
    put = done_of(page, "change_put_back", chosen_of(done_of(page, "done_opened", "nl", None), PRICE), "nl")
    clock.later()
    return page, prepared, route, clock, value_of(put, "Terugzetten"), put["done_note"]


@pytest.mark.parametrize("before, key, shown", [("12.50", "hand_exact_put_back_hand_row", None),
                                                ("12.40", "hand_exact_put_back_other_row", ("12,40", "12.40")),
                                                ("", "hand_exact_put_back_empty_row", None),
                                                (None, "hand_exact_put_back_unread_row", None)])
def test_the_button_never_writes_a_put_back_and_its_row_says_what_the_route_read(tmp_path, madeup_out, before, key, shown):
    """R1 of the second review round: the button writes no put-back, whatever the route read in Exact before the aid's write of the
    change it puts back. The put-back is passed over, and nothing is sent or logged for it. Its row says, before any press, that the
    buyer enters it, and also what the connection read in Exact there when that differs from the put-back's value: another value,
    an empty field or nothing. The note of Terugdraaien in Exact names the same, after the note of the put-back made."""
    page, prepared, route, clock, back, note = written_and_put_back(tmp_path, madeup_out, before)
    lines = log_lines(prepared)
    shown_rows = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    clock.later()
    result = pressed(page, [back])
    assert len(route.calls) == 1 and log_lines(prepared) == lines            # nothing sent, nothing logged for the put-back
    assert result["note"] == NL["hand_exact_passed_one"]
    for index, (language, row) in enumerate((("nl", ROW_BACK), ("en", ROW_BACK_EN))):
        said = nl_en(key)[index].format(value=shown[index]) if shown else nl_en(key)[index]
        assert f"{row}. {said}" in shown_rows[index], language
    said = {"12.50": "", "12.40": " " + NL["hand_put_back_other"].format(old="12,50", value="12,40", source=nl_en("hand_put_back_source:route")[0]),
            "": " " + nl_en("hand_put_back_other_route_empty")[0], None: " " + nl_en("hand_put_back_other_route_unread")[0]}[before]
    assert note == NL["hand_put_back_listed"].format(row=ROW_BACK) + said


@pytest.mark.parametrize("how", ["entered_by_hand", "log_gone"])
def test_a_put_back_without_a_verified_write_before_it_is_left_to_the_buyer(tmp_path, madeup_out, how):
    """S2: without a verified line of the route for the change it puts back (the buyer entered that change by hand, or the route's
    log is gone), the button does not write a put-back: it is passed over and its row says to enter it by hand and tick it off."""
    page, prepared, route, clock, back, note = written_and_put_back(tmp_path, madeup_out, "12.50", by_hand=how == "entered_by_hand")
    if how == "log_gone":
        (prepared / LOG).unlink()
    calls = len(route.calls)
    shown = texts_of(listed_by(page, "list_opened", "nl"))
    result = pressed(page, [back])
    assert len(route.calls) == calls and log_lines(prepared) == []
    assert result["note"] == NL["hand_exact_passed_one"]
    assert f"{ROW_BACK}. {nl_en('hand_exact_put_back_hand_row')[0]}" in shown
    assert f"{ROW_BACK_EN}. {nl_en('hand_exact_put_back_hand_row')[1]}" in texts_of(listed_by(page, "list_opened", "en"))
    assert note == NL["hand_put_back_listed"].format(row=ROW_BACK)


def test_the_put_back_of_a_change_the_aid_wrote_is_not_written_even_when_the_route_read_its_value(tmp_path, madeup_out):
    """R1 of the second review round (flipped from S2 of the first): the route read 12.5 in Exact before the aid wrote 12.70, the
    value the put-back names (as a number). The button still does not write the put-back: nothing is sent or logged for it, its row
    carries the plain note that the buyer enters it, and the note of Terugdraaien carries no sentence of the route's."""
    page, prepared, route, clock, back, note = written_and_put_back(tmp_path, madeup_out, "12.5")
    lines = log_lines(prepared)
    shown = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    assert pressed(page, [back])["note"] == NL["hand_exact_passed_one"] and len(route.calls) == 1 and log_lines(prepared) == lines
    assert note == NL["hand_put_back_listed"].format(row=ROW_BACK)
    for index, row in enumerate((ROW_BACK, ROW_BACK_EN)):
        assert f"{row}. {nl_en('hand_exact_put_back_hand_row')[index]}" in shown[index]


def test_a_verified_write_whose_outcome_cannot_be_logged_is_not_marked_and_stays_in_doubt(tmp_path, madeup_out, monkeypatch):
    """S3: the route wrote and read the value back, but its outcome line cannot be appended to the log: the change is not marked,
    since the log would not hold the value read before; the attempt stands for a write in doubt, and the press counts it so."""
    from aid.execution import exact_route
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds the log")
    monkeypatch.setattr(exact_route, "log_outcome", held)
    clock.later()
    result = pressed(page, [price])
    monkeypatch.undo()
    assert len(route.calls) == 1 and mark_lines(prepared) == [] and [line["outcome"] for line in log_lines(prepared)] == [None]
    assert result["note"] == NL["hand_exact_unsure_one"]
    clock.later()
    assert f"{PRICE_ROW}. {NL['hand_exact_unsure_row']}" in texts_of(listed_by(page, "list_opened", "nl"))
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_passed_one"] and len(route.calls) == 1


def test_a_write_whose_change_a_newer_row_replaced_during_the_call_says_that_the_newer_row_asks_another_value(tmp_path, madeup_out):
    """S4: the route put the value into Exact, but the runner replaced the change meanwhile, so the mark writes nothing: the note
    says that the old value is in Exact and that the newer row asks another value, to enter or drop, not to tick the row off."""
    def replaced(change):                        # the runner prepares a newer value of the change's place during the call
        if change.kind == "po_line_price":
            newer = WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.90")
        else:
            newer = WriteOp(kind="article_price", po_number="250108", version=1, article_code="KM-0801", new_value="12.90")
        PreparedWriter(folder[0]).apply(newer, change.case_ids[0], "R04", {}, datetime(2026, 11, 12, 9, 40))
    folder = []
    page, prepared, d, clock = route_page(tmp_path, madeup_out, StandIn(during=replaced), cases=("MU08",))
    folder.append(prepared)
    listed = listed_by(page, "list_opened", "nl")
    clock.later()
    result = pressed(page, [value_of(listed, PRICE), value_of(listed, ARTICLE)])
    assert mark_lines(prepared) == [] and [line["outcome"] for line in log_lines(prepared)] == [None, "verified", None, "verified"]
    assert result["note"] == nl_en("hand_exact_replaced_many")[0].format(n=2)
    assert "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,50 → 12,90" in texts_of(result)
    other, other_prepared, _, other_clock = route_page(tmp_path / "en", madeup_out, StandIn(during=replaced), cases=("MU08",))
    folder[0] = other_prepared
    other_clock.later()
    alone = pressed(other, [value_of(listed_by(other, "list_opened", "en"), "price 12.50")], "en")
    assert mark_lines(other_prepared) == [] and alone["note"] == nl_en("hand_exact_replaced_one")[1]


def test_a_verified_write_whose_mark_was_refused_is_not_written_again_and_its_row_says_to_tick_it_off(tmp_path, madeup_out, monkeypatch):
    """S5: the route wrote and read the value back, but the file of the marks was held at the mark. The change is in Exact and not
    ticked off: a later press passes it over without calling the route, and its row says to tick it off; the buyer's tick settles
    it."""
    from aid.review import page as page_module
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds marks.jsonl")
    monkeypatch.setattr(page_module, "mark", held)
    clock.later()
    first = pressed(page, [price])
    monkeypatch.undo()
    assert len(route.calls) == 1 and mark_lines(prepared) == [] and first["note"] == NL["hand_exact_unmarked_one"]
    clock.later()
    again = pressed(page, [price])
    assert len(route.calls) == 1 and [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]
    assert again["note"] == NL["hand_exact_passed_one"]
    clock.later()
    assert f"{PRICE_ROW}. {nl_en('hand_exact_unticked_row')[0]}" in texts_of(listed_by(page, "list_opened", "nl"))
    assert f"{PRICE_ROW_EN}. {nl_en('hand_exact_unticked_row')[1]}" in texts_of(listed_by(page, "list_opened", "en"))
    clock.later()
    ticked = listed_by(page, "changes_entered", [price], "nl")
    assert ticked["note"] == NL["hand_entered_one"] and PRICE_ROW not in " ".join(texts_of(ticked)) and len(route.calls) == 1


def test_a_mark_made_at_the_time_of_the_verified_line_settles_it(tmp_path, madeup_out):
    """S5: a press marks the change at the time of its own outcome line (one clock, one moment), and that mark settles the verified
    line ("at or after" it): once a newer print ended the aid's tick, the row is open again without a note, and a press writes it."""
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    pressed(page, [price])
    assert {line["at"] for line in mark_lines(prepared)} == {log_lines(prepared)[-1]["at"]} == {"2026-11-12T09:01:00"}
    clock.later()
    ended_by_print(prepared, ids_of(price), clock(), "12.50")
    clock.later()
    listed = listed_by(page, "list_opened", "nl")
    [row] = [text for text in texts_of(listed) if text.startswith(PRICE_ROW)]
    assert nl_en("hand_exact_unticked_row")[0] not in row
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"] and len(route.calls) == 2


def test_the_press_sends_only_a_value_in_the_aids_own_normal_form(tmp_path, madeup_out):
    """S6: the button sends a new value only in the normal form the aid's own writers give it: a plain decimal with a point (no sign,
    no exponent, no space, no comma, no NaN) for a price or a quantity, a real day as yyyy-mm-dd for a date. Rows that a hand-edited
    or damaged list holds otherwise are passed over; the same list's normal values are written."""
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    odd = [("po_line_price", " 12.70"), ("po_line_price", "12,70"), ("po_line_price", "abc"), ("po_line_price", "NaN"),
           ("po_line_quantity", "-1"), ("po_line_quantity", "1e3"), ("po_line_date", "2026-02-30"), ("po_line_date", "2026-13-45")]
    normal = [("po_line_price", "12.5"), ("po_line_quantity", "60"), ("po_line_date", "2026-11-23")]
    olds = {"po_line_price": "12.50", "po_line_quantity": "50", "po_line_date": "2026-11-16"}
    writer = PreparedWriter(prepared)
    for line, (kind, new) in enumerate([*odd, *normal], start=11):
        writer.apply(WriteOp(kind=kind, po_number="250108", version=1, line_id=str(line), old_value=olds[kind], new_value=new), case_id,
                     "R04", {}, datetime(2026, 11, 12, 9, 30))
    values = {change.line_id: "+".join(change.ids) for change in open_changes(prepared)}
    clock.later()
    for line, (kind, new) in enumerate(odd, start=11):
        result = pressed(page, [values[str(line)]])
        assert route.calls == [] and log_lines(prepared) == [], new
        assert result["note"] == NL["hand_exact_passed_one"], new
    for line, (kind, new) in enumerate(normal, start=11 + len(odd)):
        assert pressed(page, [values[str(line)]])["note"] == NL["hand_exact_written_one"], new
    assert [change.new_value for change, _ in route.calls] == [new for _, new in normal]


def test_the_gate_takes_the_settings_and_their_unread_flag_from_one_look(tmp_path, madeup_out, monkeypatch):
    """S7: the press's own look finds the settings file half written (it does not read, so the settings in use, with the test mode
    on, are kept); another handler's look, just after it, reads the finished file, in which the owner turned the test mode off. The
    press judges the settings and whether they could be read from its own look alone: nothing is sent."""
    from aid.review import page as page_module
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    path = tmp_path / "settings.json"
    text, stamp = path.read_text(encoding="utf-8"), path.stat().st_mtime_ns
    finished = json.dumps(json.loads(text) | {"test_mode": False}, indent=4)
    path.write_text(text + "\n{", encoding="utf-8")                     # the owner's save is half written at the press's look
    os.utime(path, ns=(stamp + 10**9, stamp + 10**9))
    armed = [True]

    def other_look(live) -> None:                                       # another handler's look, right after the press's own
        armed.clear()
        path.write_text(finished, encoding="utf-8")
        os.utime(path, ns=(stamp + 2 * 10**9, stamp + 2 * 10**9))
        real_now(live)
    real_now = page_module._Live.now

    def now(live):
        deps = real_now(live)
        if armed:
            other_look(live)
        return deps
    monkeypatch.setattr(page_module._Live, "now", now)
    if hasattr(page_module._Live, "look"):                              # the look of one press, once there is one
        real_look = page_module._Live.look

        def look(live):
            pair = real_look(live)
            if armed:
                other_look(live)
            return pair
        monkeypatch.setattr(page_module._Live, "look", look)
    clock.later()
    result = pressed(page, [price])
    assert not armed and route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:settings']}"


@pytest.mark.parametrize("made", ["kind", "reason", "before"])
def test_an_outcome_that_does_not_validate_is_taken_as_not_known(tmp_path, madeup_out, made):
    """S8: a route that hands back an Outcome built past its checks (model_construct) with an unknown kind, the text of an error as
    its reason, or a `before` that is no text is taken as not_known with the page's own reason: the error's text is kept nowhere,
    the change is not marked, and the row is a write in doubt."""
    from aid.execution.exact_route import Outcome
    error = "Error 500 at https://exact.example.invalid/api/orders/250108"
    given = {"kind": Outcome.model_construct(kind="done", reason=error, before=None),
             "reason": Outcome.model_construct(kind="verified", reason=error, before="12.50"),
             "before": Outcome.model_construct(kind="verified", reason="read_back", before=Decimal("12.50"))}[made]
    route = StandIn(given)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert len(route.calls) == 1 and mark_lines(prepared) == []
    assert [(line["outcome"], line["reason"], line["before"]) for line in log_lines(prepared)] == [(None, None, None),
                                                                                                 ("not_known", "no_outcome", None)]
    assert "example.invalid" not in (prepared / LOG).read_text(encoding="utf-8")
    assert result["note"] == NL["hand_exact_unsure_one"]


@pytest.mark.parametrize("where", ["before_the_attempt", "after_the_attempt"])
def test_an_unforeseen_error_of_one_row_keeps_what_the_press_did_before_it_in_the_note(tmp_path, madeup_out, monkeypatch, where):
    """S8: an error nobody foresaw, at the second of two rows, does not replace the press's note: the first row stays counted as put
    into Exact, the second counts as not put in when its attempt was not logged yet and as not sure once it was, and the press stops
    with a sentence of its own."""
    from aid.execution import exact_route
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    listed = listed_by(page, "list_opened", "nl")
    price, article = value_of(listed, PRICE), value_of(listed, ARTICLE)
    name = "doubt_of" if where == "before_the_attempt" else "log_outcome"
    real, seen = getattr(exact_route, name), []

    def second_breaks(*args, **kwargs):
        seen.append(name)
        if len(seen) == 2:
            raise RuntimeError("made-up: something nobody foresaw")
        return real(*args, **kwargs)
    monkeypatch.setattr(exact_route, name, second_breaks)
    clock.later()
    result = pressed(page, [price, article], "en")
    before = where == "before_the_attempt"
    assert [change.ids for change, _ in route.calls] == ([ids_of(price)] if before else [ids_of(price), ids_of(article)])
    assert [line["outcome"] for line in log_lines(prepared)] == ([None, "verified"] if before else [None, "verified", None])
    assert [(line["entry_id"], line["by"]) for line in mark_lines(prepared)] == [(entry_id, "exact") for entry_id in ids_of(price)]
    assert result["note"] == " ".join([EN["hand_exact_written_one"], EN["hand_exact_not_made_one" if before else "hand_exact_unsure_one"],
                                       nl_en("hand_exact_stop:error")[1]])


def test_the_route_gets_a_copy_of_the_change_and_the_mark_follows_the_pages_own(tmp_path, madeup_out):
    """S9: the route is given a copy of the change: a connector that changes what it was given (here its entry ids, to those of the
    article's price) cannot move the mark or the log onto a change that was never written."""
    moved = []

    def moves(change):
        change.ids[:] = moved
    route = StandIn(during=moves)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    listed = listed_by(page, "list_opened", "nl")
    price, article = value_of(listed, PRICE), value_of(listed, ARTICLE)
    moved.extend(ids_of(article))
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    states = {tuple(change.ids): change.state for change in changes(prepared)[0]}
    assert states[tuple(ids_of(price))] == "entered" and states[tuple(ids_of(article))] == "open"
    assert [line["entry_id"] for line in mark_lines(prepared)] == ids_of(price)
    assert [line["entry_ids"] for line in log_lines(prepared)] == [ids_of(price), ids_of(price)]


def test_an_attempt_that_cannot_be_appended_to_a_log_that_still_reads_sends_nothing(tmp_path, madeup_out, monkeypatch):
    """S10: the route's log reads (a press before left two lines in it), but the attempt cannot be appended: nothing is sent or
    marked, and the note gives the log's sentence."""
    from aid.execution import exact_route
    route = StandIn(outcome("not_made", "refused"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_not_made_one"]
    lines = log_lines(prepared)

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds the log")
    monkeypatch.setattr(exact_route, "_append_line", held)
    clock.later()
    result = pressed(page, [price])
    assert len(route.calls) == 1 and mark_lines(prepared) == [] and log_lines(prepared) == lines and len(lines) == 2
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:log']}"


# --- the review round of 17 November: the page, its texts and its ticks (the triage's P2 to P5, P10, P11) ---

def test_a_note_of_the_button_after_a_print_note_takes_no_second_full_stop(tmp_path, madeup_out, monkeypatch):
    """P2: a row's note of the button follows a note of the prints that ends with a full stop already (a print ended the buyer's
    tick; Exact shows a third value now) after one space, never after a second full stop."""
    from aid.review import hand_list
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    listed_by(page, "changes_entered", [price], "nl")                  # the buyer ticks the row,
    clock.later()
    ended_by_print(prepared, ids_of(price), clock(), "12.50")          # a print ends the tick,
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_unsure_one"] and len(route.calls) == 1      # and Exact gives no answer
    clock.later()
    for language, row, shown in (("nl", PRICE_ROW, "12,50"), ("en", PRICE_ROW_EN, "12.50")):
        strings = STRINGS[language]
        texts = texts_of(listed_by(page, "list_opened", language))
        assert f"{row}. {strings['hand_tick_ended'].format(value=shown)} {strings['hand_exact_unsure_row']}" in texts, language
        assert not [text for text in texts if ".." in text], language
    other_route = StandIn(outcome("not_known", "no_answer"))
    other, _, _, other_clock = route_page(tmp_path / "shows", madeup_out, other_route, cases=("MU08",))
    monkeypatch.setattr(hand_list, "note_value", lambda change, store, cases_root: "12.80" if change.kind == "po_line_price" else None)
    value = value_of(listed_by(other, "list_opened", "nl"), PRICE)
    other_clock.later()
    pressed(other, [value])
    for language, row, shown in (("nl", PRICE_ROW, "12,80"), ("en", PRICE_ROW_EN, "12.80")):
        strings = STRINGS[language]
        texts = texts_of(listed_by(other, "list_opened", language))
        assert f"{row}. {strings['hand_exact_shows'].format(value=shown)} {strings['hand_exact_unsure_row']}" in texts, language


def test_a_list_that_cannot_be_read_at_the_press_is_named_once(tmp_path, madeup_out, monkeypatch):
    """P3: when the list cannot be read at the press, the press sends nothing and its note names that once, also when the read of
    the list after the press fails the same way."""
    from aid.review import page as page_module
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def unread(*args, **kwargs):
        raise ListUnread("made-up: the file was saved from a spreadsheet")
    monkeypatch.setattr(page_module, "changes", unread)
    for language in ("nl", "en"):
        strings = STRINGS[language]
        result = pressed(page, [price], language)
        assert route.calls == [] and log_lines(prepared) == [] and result["tab"]["label"] == strings["hand_tab_unread"]
        assert result["note"] == f"{strings['hand_exact_not_made_one']} {strings['hand_list_changed']}", language


@pytest.mark.parametrize("shown, key", [("12.50", "hand_tick_ended_exact"), (None, "hand_tick_ended_unfound_exact")])
def test_a_tick_the_aid_made_that_a_print_ended_says_that_the_aid_put_it_into_exact(tmp_path, madeup_out, shown, key):
    """P4: a print that ends a tick the aid made (the mark "entered" by "exact") says that the assistant put the change into Exact,
    not that the buyer ticked it: by_hand keeps who made the tick that the print ended (Change.ended_by)."""
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    clock.later()
    ended_by_print(prepared, ids_of(price), clock(), shown)
    clock.later()
    for index, (language, row) in enumerate((("nl", PRICE_ROW), ("en", PRICE_ROW_EN))):
        text = nl_en(key)[index].format(value=(shown or "").replace(".", "," if language == "nl" else "."))
        assert f"{row}. {text}" in texts_of(listed_by(page, "list_opened", language)), language
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(price)]
    assert change.ended == ("version 2", shown) and change.ended_by == "exact"


@pytest.mark.parametrize("kind", ["not_verified", "not_known", "handed_over"])
def test_a_press_clears_the_ticks_of_the_rows_it_leaves_in_doubt(tmp_path, madeup_out, monkeypatch, kind):
    """P10: after a press the rows it left in doubt (not sure, handed over, or passed over because an earlier press left them in
    doubt) are no longer ticked, so that "Aangevinkte regels zijn ingevoerd" beside it cannot mark them without the look in Exact;
    the rows passed over for any other reason, not put in, or in Exact but not ticked off keep their ticks."""
    from aid.review import page as page_module
    route = StandIn(outcome(kind, "made_up"), outcome("not_made", "refused"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    listed = listed_by(page, "list_opened", "nl")
    price, date, article = (value_of(listed, words) for words in (PRICE, "leverdatum", ARTICLE))
    clock.later()
    first = pressed(page, [price, date, article])
    assert [change.ids for change, _ in route.calls] == [ids_of(price), ids_of(article)]
    assert first["rows"]["value"] == [date, article]                    # the row in doubt is no longer ticked
    clock.later()
    again = pressed(page, [price, date])                                # ticked again: passed over, and its tick cleared again
    assert len(route.calls) == 2 and again["note"] == NL["hand_exact_passed_many"].format(n=2) and again["rows"]["value"] == [date]
    # a write in Exact whose row could not be ticked off keeps its tick, the first press and the next
    other_route = StandIn(outcome("verified", "read_back", "12.50"))
    other, _, _, other_clock = route_page(tmp_path / "unmarked", madeup_out, other_route, cases=("MU08",))
    value = value_of(listed_by(other, "list_opened", "nl"), PRICE)

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds marks.jsonl")
    monkeypatch.setattr(page_module, "mark", held)
    other_clock.later()
    assert pressed(other, [value])["rows"]["value"] == [value]
    monkeypatch.undo()
    other_clock.later()
    assert pressed(other, [value])["rows"]["value"] == [value] and len(other_route.calls) == 1


# --- the review round of 17 November: what the earlier tests left unpinned (the triage's P7, P8, P9 and the lock per row) ---

def test_a_tick_that_a_print_ended_before_a_write_in_doubt_does_not_settle_it(tmp_path, madeup_out):
    """P7 (a): only a mark made after the line of a write in doubt settles it. The buyer ticked the row at 09:01, a print ended the
    tick at 09:02, and the press of 09:03 got no clear answer: the tick of 09:01 does not settle that doubt, so the press of 09:04
    passes the row over without calling the route."""
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    listed_by(page, "changes_entered", [price], "nl")
    clock.later()
    ended_by_print(prepared, ids_of(price), clock(), "12.50")
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_unsure_one"]
    clock.later()
    again = pressed(page, [price])
    assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"]


def test_a_write_in_doubt_dropped_and_put_on_the_list_again_stays_in_doubt(tmp_path, madeup_out):
    """P7 (b): only the marks "entered" and "seen" settle a write in doubt. A row in doubt (09:01) dropped with "Aangevinkte regels
    niet invoeren" (09:02) and put back with "Weer op de lijst zetten" (09:03) is still in doubt: its row says so, and the press of
    09:04 passes it over."""
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    pressed(page, [price])
    clock.later()
    assert listed_by(page, "changes_dropped", [price], "nl")["note"] == NL["hand_dropped_one"]
    clock.later()
    assert done_of(page, "change_reopened", price, "nl")["done_note"] == NL["hand_reopened"]
    clock.later()
    assert f"{PRICE_ROW}. {NL['hand_exact_unsure_row']}" in texts_of(listed_by(page, "list_opened", "nl"))
    again = pressed(page, [price])
    assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"]


def test_the_gate_is_judged_again_for_each_row(tmp_path, madeup_out):
    """P8: the gate is judged for each row, not once for the press: the owner turns the test mode off while the route writes the
    first of two rows, and the second is not sent."""
    folder = []

    def test_mode_off(change):
        path = folder[0] / "settings.json"
        stamp = path.stat().st_mtime_ns
        path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"test_mode": False}, indent=4), encoding="utf-8")
        os.utime(path, ns=(stamp + 10**9, stamp + 10**9))
    route = StandIn(during=test_mode_off)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    folder.append(tmp_path)
    listed = listed_by(page, "list_opened", "nl")
    clock.later()
    result = pressed(page, [value_of(listed, PRICE), value_of(listed, ARTICLE)])
    assert len(route.calls) == 1
    assert result["note"] == " ".join([NL["hand_exact_written_one"], NL["hand_exact_not_made_one"], NL["hand_exact_stop:test_mode"]])


@pytest.mark.parametrize("file", [LOG, MARKS_FILE])
def test_a_line_that_cannot_be_read_does_not_lift_a_write_in_doubt(tmp_path, madeup_out, file):
    """P9: the route's log and the file of the marks are read leniently: a line that cannot be read (here one cut off as no JSON)
    is passed over, so the row in doubt still says so and a press still passes it over."""
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    pressed(page, [price])
    with open(prepared / file, "a", encoding="utf-8") as f:
        f.write("{not json")
    clock.later()
    listed = listed_by(page, "list_opened", "nl")
    assert listed["tab"]["label"] == NL["hand_tab_count"].format(n=2)        # the list still reads, not "In Exact invoeren (?)"
    assert f"{PRICE_ROW}. {NL['hand_exact_unsure_row']}" in texts_of(listed)
    again = pressed(page, [price])
    assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"]


def test_two_presses_of_one_row_at_once_call_the_route_once(tmp_path, madeup_out, monkeypatch):
    """The lock per row: two presses of one row at once (two browser tabs) call the route once. The route blocks until the test lets
    it go, and each press is held for a moment between its checks and its attempt line, so that without the lock both would pass
    their checks before either logged its attempt."""
    import threading

    from aid.execution import exact_route
    called, release = threading.Event(), threading.Event()

    class Blocking(StandIn):
        def write(self, change, now):
            self.calls.append((change, now))
            called.set()
            release.wait(30)
            return outcome("verified", "read_back", change.old_value)
    route = Blocking()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    meet, real = threading.Barrier(2, timeout=5), exact_route.log_attempt

    def at_the_attempt(*args, **kwargs):
        try:
            meet.wait()                          # the other press meets this one here only when no lock keeps it out
        except threading.BrokenBarrierError:
            pass
        return real(*args, **kwargs)
    monkeypatch.setattr(exact_route, "log_attempt", at_the_attempt)
    notes: list[str] = []
    presses = [threading.Thread(target=lambda: notes.append(pressed(page, [price])["note"])) for _ in range(2)]
    for press in presses:
        press.start()
    assert called.wait(30)
    release.set()
    for press in presses:
        press.join(60)
    assert len(route.calls) == 1 and [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]
    assert sorted(notes) == sorted([NL["hand_exact_written_one"], NL["hand_exact_passed_one"]])


def test_the_documents_say_what_the_review_round_of_the_button_settled():
    """MANUAL 2.6 quotes what the button says after this round, and says that the ticks of rows in doubt go and how a try-out and a
    value are judged; 3.10 says that the button never puts a change back (the rule of the second review round, R1 and R7), names the
    verified write not ticked off, the try-out judged at the press and the clocks a doubt is compared by; the plug point's docstring
    names the clocks and the page's lock during a call."""
    manual = manual_sections()
    two_six = " ".join(manual["2.6"].split())
    for key, value in (("hand_exact_unticked_row", None), ("hand_exact_replaced_one", None), ("hand_exact_stop:error", None),
                       ("hand_exact_put_back_other_row", "12,40"), ("hand_exact_put_back_hand_row", None), ("hand_tick_ended_exact", "12,50")):
        assert f"\"{NL[key].format(value=value) if value else NL[key]}\"" in two_six, key
    for words in ("After a press such a row is no longer ticked", "in the form in which the aid itself writes it",
                  "a document it cannot read, or a case that is gone, counts as a try-out's", "but for the rows left in doubt"):
        assert words in two_six, words
    three_ten = " ".join(manual["3.10"].split())
    for words in ("a clock set back between the two could keep a doubt too long or lift it too early",
                  "`mark_for_writing` of `aid/tryout_mark.py`", "is in Exact with its row not ticked off",
                  "The button for Exact on the tab \"In Exact invoeren\" (section 2.6) never puts a change back",
                  "The button never writes a put-back."):
        assert words in three_ten, words
    from aid.execution import exact_route
    described = " ".join((exact_route.__doc__ or "").split())
    assert "a clock set back between the two could keep a doubt too long or lift it too early" in described
    assert "a slow write makes a press of the button in another browser tab wait" in described


# --- the second review round of 17 November: the button and its holds (the triage's R1, R3, R4, R5, R13 and R14) ---

UNDONE_ROW = "Inkooporder 250108 (S08), regel 1 (KM-0801): prijs 12,70 → 12,50"           # an undo row that no mark puts_back links
UNDONE_ROW_EN = "Purchase order 250108 (S08), line 1 (KM-0801): price 12.70 → 12.50"


@pytest.mark.parametrize("how", ["written", "entered_by_hand"])
def test_an_undo_row_of_the_manuals_recipe_is_not_written(tmp_path, madeup_out, how):
    """R1: MANUAL 3.10's recipe (undo_case through the runner's writer) prepares an undo row that no mark puts_back links. The button
    judges a change by its rows as prepared_changes.csv holds them at the press, not by the rule id of its newest row: a change that
    holds an undo row, linked or not, is a put-back, and it is never written. Its row says that the buyer enters it, whether the aid
    put the change into Exact (the route having read the undo's own value there) or the buyer entered it by hand."""
    from aid.execution.writer import undo_case
    from aid.runner import build_deps
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    if how == "written":
        assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    else:
        assert listed_by(page, "changes_entered", [price], "nl")["note"] == NL["hand_entered_one"]
    clock.later()
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    undo_case(d.settings.cases_root / case_id, build_deps(d.settings, {}).writer, clock())
    clock.later()
    calls, lines = len(route.calls), log_lines(prepared)
    shown = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    back = value_of(listed_by(page, "list_opened", "nl"), "prijs 12,70 → 12,50")
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(back)]
    assert change.puts_back is None                                          # the recipe links nothing
    clock.later()
    result = pressed(page, [back])
    assert len(route.calls) == calls and log_lines(prepared) == lines and result["note"] == NL["hand_exact_passed_one"]
    for index, row in enumerate((UNDONE_ROW, UNDONE_ROW_EN)):
        assert f"{row}. {nl_en('hand_exact_put_back_hand_row')[index]}" in shown[index]


def test_a_put_back_whose_link_could_not_be_written_is_not_written(tmp_path, madeup_out, monkeypatch):
    """R1: "Terugdraaien in Exact" prepares the put-back of a change the aid put into Exact and then cannot link it (another program
    holds marks.jsonl at that moment), so the row stands on the list without the mark puts_back. The button judges it by its rows
    all the same: it holds an undo row, so nothing is sent or logged, and its row says that the buyer enters it."""
    from aid.review import hand_list

    class LinkHeld(PermissionError):
        """made-up: another program holds marks.jsonl at the link"""

    def held(*args, **kwargs):
        raise LinkHeld("made-up: another program holds marks.jsonl")
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    clock.later()
    monkeypatch.setattr(hand_list, "link_put_back", held)
    put = done_of(page, "change_put_back", chosen_of(done_of(page, "done_opened", "nl", None), PRICE), "nl")
    monkeypatch.undo()
    assert put["done_note"] == NL["settings_failed"].format(code="LinkHeld")
    clock.later()
    calls, lines = len(route.calls), log_lines(prepared)
    shown = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    back = value_of(listed_by(page, "list_opened", "nl"), "prijs 12,70 → 12,50")
    assert [change.puts_back for change in open_changes(prepared) if change.ids == ids_of(back)] == [None]
    clock.later()
    result = pressed(page, [back])
    assert len(route.calls) == calls and log_lines(prepared) == lines and result["note"] == NL["hand_exact_passed_one"]
    for index, row in enumerate((UNDONE_ROW, UNDONE_ROW_EN)):
        assert f"{row}. {nl_en('hand_exact_put_back_hand_row')[index]}" in shown[index]


@pytest.mark.parametrize("before", ["12.50", "12.40", "", None])
def test_a_put_back_of_a_change_the_buyer_ticked_after_a_print_ended_the_aids_tick_is_not_written(tmp_path, madeup_out, before):
    """R1, the sequence of the second safety review's finding 2: the route put 12.70 into Exact, having read 12.50 there (the
    put-back's own value), 12.40, an empty field or nothing. Version 2 of the order, filed after that write, shows 12.60 for the line,
    so the real contradict of by_hand ends the aid's tick; the buyer then enters the change in Exact and ticks it off, as the row
    asks, and presses Terugdraaien in Exact. The put-back is not written and nothing is logged; its row carries the plain note in
    both languages, whatever the route read before the aid's write, since the change it puts back is no longer marked entered by the
    aid (T12 of the third review round: the condition of R1 for the row's note)."""
    from aid.execution.by_hand import contradict
    from aid.tests.fakes import line
    from aid.tests.test_by_hand import filed
    route = StandIn(outcome("verified", "read_back", before))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    clock.later(59)
    filed(d.store, 2, line("KM-0801", "3", "12.60", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", date="2026-11-16", basis="1"),
          po="250108", at=clock())
    clock.later()
    assert contradict(prepared, d.store, d.settings.cases_root, clock()) == 1
    assert {(one["mark"], one["by"], one.get("shown")) for one in mark_lines(prepared)[-len(ids_of(price)):]} == {("reopened", "version 2", "12.60")}
    clock.later()
    listed = listed_by(page, "list_opened", "nl")
    assert f"{PRICE_ROW}. {nl_en('hand_tick_ended_exact')[0].format(value='12,60')}" in texts_of(listed)
    assert listed_by(page, "changes_entered", [value_of(listed, PRICE)], "nl")["note"] == NL["hand_entered_one"]
    clock.later()
    put = done_of(page, "change_put_back", chosen_of(done_of(page, "done_opened", "nl", None), PRICE), "nl")
    clock.later()
    back, lines = value_of(put, "Terugzetten"), log_lines(prepared)
    shown = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    clock.later()
    result = pressed(page, [back])
    assert len(route.calls) == 1 and log_lines(prepared) == lines and result["note"] == NL["hand_exact_passed_one"]
    for index, row in enumerate((ROW_BACK, ROW_BACK_EN)):
        assert f"{row}. {nl_en('hand_exact_put_back_hand_row')[index]}" in shown[index]


def test_a_change_with_an_entry_whose_row_cannot_be_read_at_the_press_is_not_written(tmp_path, madeup_out, monkeypatch):
    """R1: the press reads the rows of prepared_changes.csv again to judge the change by them. An entry of the change whose row that
    read does not give (its line damaged since the list was read; forced here by patching the read) may be an undo row, so the
    change is passed over: nothing is sent or logged."""
    from aid.execution import by_hand, exact_route
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def without_the_price(folder, kinds):
        return [row for row in by_hand.prepared_rows(folder, kinds) if row.entry_id not in ids_of(price)]
    monkeypatch.setattr(exact_route, "prepared_rows", without_the_price, raising=False)
    clock.later()
    result = pressed(page, [price])
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == NL["hand_exact_passed_one"] and result["rows"]["value"] == [price]


@pytest.mark.parametrize("setup", ["in_doubt", "not_ticked_off"])
@pytest.mark.parametrize("by", [5, [], True, {"x": 1}])
def test_a_mark_whose_by_is_no_text_is_passed_over_by_the_list_and_by_the_button_alike(tmp_path, madeup_out, monkeypatch, setup, by):
    """R3: the list and the button's hold read marks.jsonl through one parser of by_hand, so that they cannot disagree on a line. A
    line "entered" whose `by` is no text (no writer of the aid writes one; a hand edit or another program might) is passed over by
    both: after a write in doubt, and after a verified write whose row could not be ticked off, it settles nothing. The row keeps its
    note, the intro counts the line that cannot be read, and a press does not call the route again."""
    from aid.review import page as page_module
    route = StandIn(outcome("not_known", "no_answer") if setup == "in_doubt" else outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    if setup == "not_ticked_off":
        def held(*args, **kwargs):
            raise PermissionError("made-up: another program holds marks.jsonl")
        monkeypatch.setattr(page_module, "mark", held)
    clock.later()
    pressed(page, [price])
    monkeypatch.undo()
    clock.later()
    with open(prepared / MARKS_FILE, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps({"entry_id": entry_id, "mark": "entered", "at": clock().isoformat(), "by": by}) + "\n"
                        for entry_id in ids_of(price)))
    clock.later()
    listed, lines = listed_by(page, "list_opened", "nl"), log_lines(prepared)
    clock.later()
    again = pressed(page, [price])
    assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"] and log_lines(prepared) == lines
    note = NL["hand_exact_unsure_row"] if setup == "in_doubt" else nl_en("hand_exact_unticked_row")[0]
    assert f"{PRICE_ROW}. {note}" in texts_of(listed)
    passed = len(ids_of(price))
    assert listed["intro"].endswith(NL["hand_unreadable_one"] if passed == 1 else NL["hand_unreadable_many"].format(n=passed))


@pytest.mark.parametrize("read", ["changes", "mark_for_writing", "doubt_of"])
def test_the_gate_is_judged_again_after_the_rows_own_reads(tmp_path, madeup_out, monkeypatch, read):
    """R4: the gate is judged before the row's reads and again as the last step before the attempt is logged. The owner turns the
    test mode off while the press reads the list, the documents of the change's cases or the route's log for its one row: nothing is
    sent or logged, and the note says why."""
    from aid.execution import exact_route
    from aid.review import page as page_module
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    path, module = tmp_path / "settings.json", exact_route if read == "doubt_of" else page_module
    real, armed = getattr(module, read), [True]

    def test_mode_off_meanwhile(*args, **kwargs):
        if armed:                                # once: the press's own read of its row
            armed.clear()
            stamp = path.stat().st_mtime_ns
            path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"test_mode": False}, indent=4), encoding="utf-8")
            os.utime(path, ns=(stamp + 10**9, stamp + 10**9))
        return real(*args, **kwargs)
    monkeypatch.setattr(module, read, test_mode_off_meanwhile)
    clock.later()
    result = pressed(page, [price])
    assert not armed and route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:test_mode']}"


def test_the_target_is_read_once_after_available_and_that_value_is_judged_and_logged(tmp_path, madeup_out):
    """R4: the last check reads the route's target once, after available(), judges it, and gives that same value to both lines of the
    log. A route whose target turns to "live" inside available() is not written to and nothing is logged; a target that turns during
    the call does not change the target the outcome line names."""
    class Turning(StandIn):
        def available(self) -> bool:
            self.target = "live"
            return True
    route = Turning()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:live']}"
    turned = StandIn(during=lambda change: setattr(turned, "target", "live"))
    other, other_prepared, _, other_clock = route_page(tmp_path / "during", madeup_out, turned, cases=("MU08",))
    value = value_of(listed_by(other, "list_opened", "nl"), PRICE)
    other_clock.later()
    assert pressed(other, [value])["note"] == NL["hand_exact_written_one"]
    assert [(line["outcome"], line["target"]) for line in log_lines(other_prepared)] == [(None, "test"), ("verified", "test")]


@pytest.mark.parametrize("answer", ["no", "coroutine"])
def test_available_counts_as_open_only_when_it_returns_true(tmp_path, madeup_out, answer):
    """R4: available() opens the gate only when it returns True itself. A connector outside its contract whose available() returns
    a text, or the coroutine of an async available() that nobody awaits, writes nothing: nothing is sent or logged, and the note
    says that the connection is not available."""
    made = []

    class Odd(StandIn):
        def available(self):
            if answer == "no":
                return "no"

            async def up() -> bool:
                return True
            made.append(up())
            return made[-1]
    route = Odd()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    try:
        result = pressed(page, [price])
    finally:
        for coroutine in made:                   # never awaited: closed, so that no warning is left for the garbage collector
            coroutine.close()
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:unavailable']}"


def test_a_write_whose_change_a_case_withdrew_during_the_call_says_its_row_was_not_ticked_off(tmp_path, madeup_out):
    """R14: the note "replaced" says that a newer change of the line or article stands on the list with another value, to enter or
    to drop. A case that withdraws the change during the call (the mark "replaced" by "case <id>" that by_hand.withdraw writes when
    the supplier's newest document names the value Exact holds again) leaves no such change on the list: the note is the one of a
    write whose row was not ticked off."""
    folder = []

    def withdrawn(change):
        mark(folder[0], change.ids, "replaced", folder[1](), by="case 250108_20261112T093000")
    page, prepared, d, clock = route_page(tmp_path, madeup_out, StandIn(during=withdrawn), cases=("MU08",))
    folder.extend([prepared, clock])
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]
    assert [(line["mark"], line["by"]) for line in mark_lines(prepared)] == [("replaced", "case 250108_20261112T093000")] * len(ids_of(price))
    [change] = [one for one in changes(prepared)[0] if one.ids == ids_of(price)]
    assert change.state == "replaced" and change.withdrawn
    assert result["note"] == NL["hand_exact_unmarked_one"]


# --- the second review round of 17 November: the texts, the notes and the documents (the triage's R6 to R10) ---

@pytest.mark.parametrize("ticked_by", ["buyer", "exact"])
def test_a_row_not_ticked_off_after_a_write_shows_only_the_buttons_note(tmp_path, madeup_out, monkeypatch, ticked_by):
    """R8: a print ended a tick of the row, the buyer's or the aid's after an earlier write, and the button then put the change into
    Exact again but could not tick the row off. The print's note is older than that write, since the button writes only an open
    change, and its advice to enter the change no longer holds: the row shows only the button's note, in both languages."""
    from aid.review import page as page_module
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    if ticked_by == "buyer":
        assert listed_by(page, "changes_entered", [price], "nl")["note"] == NL["hand_entered_one"]
    else:
        assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    clock.later()
    ended_by_print(prepared, ids_of(price), clock(), "12.50")
    clock.later()

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds marks.jsonl")
    monkeypatch.setattr(page_module, "mark", held)
    assert pressed(page, [price])["note"] == NL["hand_exact_unmarked_one"]
    monkeypatch.undo()
    clock.later()
    for index, (language, row) in enumerate((("nl", PRICE_ROW), ("en", PRICE_ROW_EN))):
        assert f"{row}. {nl_en('hand_exact_unticked_row')[index]}" in texts_of(listed_by(page, "list_opened", language)), language


def test_a_tick_ended_before_a_verified_write_does_not_settle_it(tmp_path, madeup_out, monkeypatch):
    """R6: a verified write is settled only by a mark "entered" or "seen" made at its time or after it. The buyer ticked the row at
    09:01, a print ended that tick at 09:02, and the press of 09:03 put the change into Exact but could not tick the row off. The tick
    of 09:01 does not settle that write: the list read at 09:04 says to tick the row off, and the press of 09:05 passes the row over,
    with one call of the route in all."""
    from aid.review import page as page_module
    route = StandIn(outcome("verified", "read_back", "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert listed_by(page, "changes_entered", [price], "nl")["note"] == NL["hand_entered_one"]
    clock.later()
    ended_by_print(prepared, ids_of(price), clock(), "12.50")
    clock.later()

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds marks.jsonl")
    monkeypatch.setattr(page_module, "mark", held)
    assert pressed(page, [price])["note"] == NL["hand_exact_unmarked_one"]
    monkeypatch.undo()
    clock.later()
    [row] = [text for text in texts_of(listed_by(page, "list_opened", "nl")) if text.startswith(PRICE_ROW)]
    assert row.endswith(nl_en("hand_exact_unticked_row")[0])
    clock.later()
    again = pressed(page, [price])
    assert again["note"] == NL["hand_exact_passed_one"] and len(route.calls) == 1
    assert [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]


def test_the_english_prose_of_the_button_calls_the_buyer_they():
    """R10: the English prose that describes the button calls the buyer "they", "them" and "themselves". The words are looked for in
    the plug point's module, in this file, in the docstrings of the page and of the list where they describe the button, in the
    button's paragraphs of the manual and in the English texts of the button."""
    from aid.execution import exact_route
    from aid.review import hand_list
    from aid.review import page as page_module
    gendered = re.compile(r"\b(?:h" r"e|h" r"im|h" r"is|h" r"imself|sh" r"e|h" r"er|h" r"ers|h" r"erself)\b", re.IGNORECASE)

    def between(text: str, start: str, end: str) -> str:
        assert start in text and end in text.partition(start)[2], start
        return start + text.partition(start)[2].partition(end)[0]
    manual = manual_sections()
    prose = {
        "exact_route.py": Path(exact_route.__file__).read_text(encoding="utf-8"),
        "test_exact_route.py": Path(__file__).read_text(encoding="utf-8"),
        "page.py": between(page_module.__doc__, "The button into Exact.", "Under the list the tab has two sections"),
        "hand_list.py": between(hand_list.__doc__, "On a page built with a route into Exact", "Under the list the tab has two sections")
                        + hand_list.intro.__doc__,
        "MANUAL 2.6": between(manual["2.6"], "**The button for Exact.**", "\n"),
        "MANUAL 3.10": between(manual["3.10"], "**The plug point of the button.**", "\n"),
        "texts": " ".join(english for _, english in TEXTS.values()),
    }
    found = {where: sorted(set(gendered.findall(text))) for where, text in prose.items() if gendered.search(text)}
    assert found == {}


def test_the_documents_say_that_the_button_never_puts_a_change_back():
    """The second review round: MANUAL 2.6 and 3.10 ("The plug point of the button" and "The undo"), the page's and the plug point's
    docstrings and the dated amendment of the design say that the button never puts a change back and that the buyer enters every
    put-back, whose row names what the connection read only while the records still speak for it (R1, R7, R9); 2.6 says that a row
    whose answer could not be noted counts as not sure whatever the answer was (R5); 3.10 says how a put-back is told by its rows,
    that the gate is judged again last (R4) and that the marks are read by the list's own reader (R3)."""
    manual = manual_sections()
    two_six = " ".join(manual["2.6"].split())
    for key, value in (("hand_exact_put_back_hand_row", None), ("hand_exact_put_back_other_row", "12,40"), ("hand_exact_not_made_one", None)):
        assert f"\"{NL[key].format(value=value) if value else NL[key]}\"" in two_six, key
    for words in ("The button never puts a change back", "also one made with the recipe of section 3.10",
                  "as long as the aid could note that answer in its log", "whatever the answer was"):
        assert words in two_six, words
    three_ten = " ".join(manual["3.10"].split())
    for words in ("The button never writes a put-back.", "not by the rule id of its newest row", "the recipe under \"The undo\" below links none",
                  "and so is a change with an entry whose row that read does not give", "The buyer enters every put-back.",
                  "again as the last step before the attempt is logged", "counts only when it returns `True`",
                  "whatever the connector answered", "the list's own reader of `marks.jsonl`",
                  "never puts a change back, not even one it put into Exact itself"):
        assert words in three_ten, words
    from aid.execution import exact_route
    from aid.review import page as page_module
    assert "The button never writes a put-back" in " ".join(exact_route.__doc__.split())
    assert "the button never puts a change back" in " ".join(page_module.__doc__.split())
    design = Path("docs/superpowers/specs/2026-11-17-click-to-exact-design.md")
    if design.exists():                          # the design stands beside aid/ in the repository, not in a copy of aid/ alone
        amended = " ".join(design.read_text(encoding="utf-8").split())
        assert "**Amendment of 17 November 2026 (no put-back through the button).**" in amended
        assert "the button writes no put-back" in amended and "can then be put into Exact with the button too" in amended


# --- the third review round of 17 November: the route path (the triage's T1 to T5) ---

def rewritten(path: Path, how: str, later: bool = True) -> None:
    """The settings file changed as the owner might change it: the test mode turned off ("test_mode"), the file cut off so that it is
    no JSON ("no_json"), the file deleted ("deleted"), exact_base_url set in it ("exact_base_url"), or a key added that the settings
    do not know ("unknown_key"); the time of the file moved a second later, or (later False) kept as it was, as a tool that keeps the
    times of files leaves it."""
    before = path.stat()
    if how == "deleted":
        path.unlink()
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text({"test_mode": json.dumps(data | {"test_mode": False}, indent=4), "no_json": "{",
                     "exact_base_url": json.dumps(data | {"exact_base_url": "https://exact.example.com/api"}, indent=4),
                     "unknown_key": json.dumps(data | {"made_up_key": 1}, indent=4)}[how],
                    encoding="utf-8")
    moved = 10**9 if later else 0
    os.utime(path, ns=(before.st_atime_ns + moved, before.st_mtime_ns + moved))


@pytest.mark.parametrize("how, cause, later", [
    pytest.param("test_mode", "test_mode", True, id="test_mode-test_mode"),
    pytest.param("no_json", "settings", True, id="no_json-settings"),
    pytest.param("deleted", "settings", True, id="deleted-settings"),
    pytest.param("exact_base_url", "settings", True, id="exact_base_url-settings"),
    pytest.param("test_mode", "test_mode", False, id="test_mode-test_mode-kept"),
    pytest.param("no_json", "settings", False, id="no_json-settings-kept"),
    pytest.param("exact_base_url", "settings", False, id="exact_base_url-settings-kept")])
def test_the_last_check_judges_the_settings_after_available(tmp_path, madeup_out, how, cause, later):
    """T1: the last check asks available() first and judges the settings only after it, so that a settings file changed while
    available() ran is seen. A connector whose available() takes a moment, during which the owner turns the test mode off, cuts the
    file off, deletes it or sets exact_base_url in it (here during its second call, the last check's): nothing is sent or logged,
    and the note says why. The file's time is moved later, or kept as it was: a rewrite that keeps the time is seen only by the last
    check's own read of the file, so that read too comes after available()."""
    class Slow(StandIn):
        asked = 0

        def available(self) -> bool:
            self.asked += 1
            if self.asked == 2:                  # the last check's call: the file changes meanwhile
                rewritten(tmp_path / "settings.json", how, later)
            return True
    route = Slow()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert route.asked == 2 and route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL[f'hand_exact_stop:{cause}']}"


@pytest.mark.parametrize("copy", ["after", "before"])
def test_an_entry_id_on_two_lines_is_an_undo_row_when_one_of_them_is(tmp_path, madeup_out, copy):
    """T2: an entry id that stands on two lines of prepared_changes.csv (a list edited by hand or damaged: the aid's writers give every
    row an entry id of its own) counts as an undo row whatever rule ids its lines give, in either order. MANUAL 3.10's recipe
    prepares the undo row of a change the aid put into Exact, and a copy of that line with the rule id "R01" is appended after it
    (the list reads the two lines as one change whose rule id is "R01") or inserted before it (the change's rule id is then the undo
    line's own, and the copy's "R01" comes first). The button still passes it over: nothing is sent or logged, and the row says, in
    both languages, that the buyer enters it."""
    import csv
    import io

    from aid.execution.writer import undo_case
    from aid.runner import build_deps
    route = StandIn(outcome("verified", "read_back", "12.40"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_written_one"]
    clock.later()
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    undo_case(d.settings.cases_root / case_id, build_deps(d.settings, {}).writer, clock())
    path = prepared / "prepared_changes.csv"
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    [undo] = [row for row in rows if row["rule_id"] == "undo" and row["kind"] == "po_line_price"]
    copied = io.StringIO()
    csv.DictWriter(copied, fieldnames=list(rows[0]), lineterminator="\n").writerow(undo | {"rule_id": "R01"})
    if copy == "after":
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(copied.getvalue())
    else:                                        # the copy above the undo line, every other line of the file as it was
        with open(path, encoding="utf-8", newline="") as f:
            lines = f.read().splitlines(keepends=True)
        [at] = [number for number, text in enumerate(lines) if undo["entry_id"] in text]
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("".join([*lines[:at], copied.getvalue(), *lines[at:]]))
    clock.later()
    back = value_of(listed_by(page, "list_opened", "nl"), "prijs 12,70 → 12,50")
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(back)]
    newest = "R01" if copy == "after" else "undo"            # the rule id of the change is that of its last line
    assert change.ids == [undo["entry_id"]] * 2 and change.rule_id == newest and change.puts_back is None      # the staging
    lines = log_lines(prepared)
    shown = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    clock.later()
    result = pressed(page, [back])
    assert len(route.calls) == 1 and log_lines(prepared) == lines and result["note"] == NL["hand_exact_passed_one"]
    for index, row in enumerate((UNDONE_ROW, UNDONE_ROW_EN)):
        assert f"{row}. {nl_en('hand_exact_put_back_hand_row')[index]}" in shown[index]


@pytest.mark.parametrize("how, time, cause", [
    pytest.param("test_mode", "kept", "test_mode", id="kept"),
    pytest.param("test_mode", "later", "test_mode", id="later"),
    pytest.param("no_json", "kept", "settings", id="no_json-kept"),
    pytest.param("exact_base_url", "kept", "settings", id="exact_base_url-kept"),
    pytest.param("unknown_key", "kept", "settings", id="unknown_key-kept")])
def test_the_last_check_reads_the_settings_file_itself(tmp_path, madeup_out, how, time, cause):
    """T3: the page reads its settings file again only when the file's modification time changed, and some rewrites keep that time
    (a tool that keeps or restores the times of files, or a file system with coarse times). The last check reads the settings file
    itself: the owner turns the test mode off in a file that keeps its earlier time, and nothing is sent or logged; the twin, whose
    time moved later, stops as well. A file that this read cannot take, its time kept (cut off so that it is no JSON, with
    exact_base_url set, or with a key the settings do not know), stops the press with the sentence of the settings: the settings
    read before do not stand in for it."""
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    rewritten(tmp_path / "settings.json", how, later=time == "later")
    clock.later()
    result = pressed(page, [price])
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL[f'hand_exact_stop:{cause}']}"


@pytest.mark.parametrize("kind, note", [("not_known", "hand_exact_unsure_row"), ("verified", "hand_exact_unticked_row")])
@pytest.mark.parametrize("at, error", [("0001-01-01T00:00:00", ValueError), ("9999-12-31T23:59:59", OverflowError)])
def test_a_line_of_the_routes_log_at_the_edge_of_the_calendar_keeps_the_change_held(tmp_path, madeup_out, monkeypatch, kind, note, at,
                                                                                    error):
    """T4: a line of the route's log whose time has no zone and lies at the edge of the calendar (year 1, year 9999) is read, but this
    PC's clock cannot place it in its zone. After a write in doubt such a line is the change's last: it keeps the change held (a
    write in doubt for a line in doubt, a write not ticked off for a verified one), as a mark with such a time is passed over, and
    does not take the tab away. The list reads, the row keeps a note, and a press passes the row over without calling the route or
    logging a line. That the zone cannot be told is forced the same on every system, by patching."""
    from aid.execution import exact_route
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_unsure_one"]
    with open(prepared / LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": at, "entry_ids": ids_of(price), "outcome": kind, "reason": "made_up", "before": None}) + "\n")
    real = exact_route._aware

    def edge(moment: datetime) -> datetime:
        if moment.year in (1, 9999):
            raise error(f"made-up: year {moment.year} cannot be placed in the zone of this PC")
        return real(moment)
    monkeypatch.setattr(exact_route, "_aware", edge)
    clock.later()
    listed = listed_by(page, "list_opened", "nl")
    assert listed["tab"]["label"] == NL["hand_tab_count"].format(n=2)          # the list reads, not "In Exact invoeren (?)"
    assert f"{PRICE_ROW}. {NL[note]}" in texts_of(listed)
    lines = log_lines(prepared)
    clock.later()
    again = pressed(page, [price])
    assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"] and log_lines(prepared) == lines


@pytest.mark.parametrize("answer", [1, "True"])
def test_the_early_check_counts_available_as_open_only_when_it_returns_true(tmp_path, madeup_out, answer):
    """T5: the early check, before a row's own reads, counts available() as open only when it returns True itself, as the last check
    does, so that the two checks agree. A connector outside its contract whose available() returns 1 or "True" stops the press at
    its first row with the sentence of a connection that is not available, also when that row is one the button would pass over (a
    week), so that the note gives the reason. Nothing is sent or logged."""
    route = StandIn(up=answer)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    listed = listed_by(page, "list_opened", "nl")
    week, price = value_of(listed, "week 48"), value_of(listed, PRICE)
    clock.later()
    for ticked in ([week], [week, price]):
        result = pressed(page, ticked)
        count = NL["hand_exact_not_made_one"] if len(ticked) == 1 else NL["hand_exact_not_made_many"].format(n=len(ticked))
        assert result["note"] == f"{count} {NL['hand_exact_stop:unavailable']}", len(ticked)
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []


# --- the third review round of 17 November: the tests the page check found missing (the triage's T9 to T13; T12 is the parametrized
# test of finding 2 above) ---

def test_an_undo_row_joined_by_a_later_row_of_the_same_value_is_still_a_put_back(tmp_path, madeup_out):
    """T9 (R1 of the second review round): a change is a put-back by its rows, not by the rule id of its newest row. The buyer ticked
    MU08's price; MANUAL 3.10's recipe prepares its undo row (12,70 → 12,50, linked to nothing), and a later row of the same case asks
    the same value at the same place, so that the list joins the two rows into one change whose newest rule id is "R04". The button
    passes it over: nothing is sent or logged, and its row says, in both languages, that the buyer enters it."""
    from aid.execution.writer import undo_case
    from aid.runner import build_deps
    route = StandIn()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert listed_by(page, "changes_entered", [price], "nl")["note"] == NL["hand_entered_one"]
    clock.later()
    [case_id] = [path.name for path in d.settings.cases_root.iterdir() if path.name.startswith("250108_")]
    undo_case(d.settings.cases_root / case_id, build_deps(d.settings, {}).writer, clock())
    clock.later()
    PreparedWriter(prepared).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.70",
                                           new_value="12.50"), case_id, "R04", {}, clock())
    clock.later()
    joined = value_of(listed_by(page, "list_opened", "nl"), "prijs 12,70 → 12,50")
    [change] = [one for one in open_changes(prepared) if one.ids == ids_of(joined)]
    assert len(change.ids) == 2 and change.rule_id == "R04" and change.puts_back is None          # the staging: one change of two rows
    shown = [texts_of(listed_by(page, "list_opened", language)) for language in ("nl", "en")]
    clock.later()
    result = pressed(page, [joined])
    assert route.calls == [] and log_lines(prepared) == [] and result["note"] == NL["hand_exact_passed_one"]
    for index, row in enumerate((UNDONE_ROW, UNDONE_ROW_EN)):
        assert f"{row}. {nl_en('hand_exact_put_back_hand_row')[index]}" in shown[index]


def test_a_target_that_turns_live_during_the_last_checks_available_is_not_written_to(tmp_path, madeup_out):
    """T10 (R4 of the second review round): the last check reads the route's target once, after available(). A route whose target
    turns to "live" only during its second call of available(), the last check's (the early check's call leaves it "test"): nothing
    is sent or logged, and the note says that the connection points to the live Exact."""
    class TurnsLate(StandIn):
        asked = 0

        def available(self) -> bool:
            self.asked += 1
            if self.asked == 2:
                self.target = "live"
            return True
    route = TurnsLate()
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert route.asked == 2 and route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:live']}"


@pytest.mark.parametrize("cause", ["test_mode", "live", "unavailable", "settings"])
def test_a_closed_gate_stops_the_press_at_a_row_it_would_pass_over_and_says_why(tmp_path, madeup_out, cause):
    """T11: the early check judges the gate before a row's own reads, so that a closed gate stops the press with its reason also at a
    row the button would pass over: a week ticked first, before a price, and a week alone. Nothing is sent or logged, and the note
    ends with the sentence of the cause."""
    route = StandIn(target="live" if cause == "live" else "test", up=cause != "unavailable")
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, test_mode=cause != "test_mode")
    listed = listed_by(page, "list_opened", "nl")
    week, price = value_of(listed, "week 48"), value_of(listed, PRICE)
    if cause == "settings":
        rewritten(tmp_path / "settings.json", "no_json")
    clock.later()
    for ticked in ([week, price], [week]):
        result = pressed(page, ticked)
        count = NL["hand_exact_not_made_many"].format(n=2) if len(ticked) == 2 else NL["hand_exact_not_made_one"]
        assert result["note"] == f"{count} {NL[f'hand_exact_stop:{cause}']}", len(ticked)
    assert route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []


def test_the_gate_is_judged_again_for_a_row_the_button_would_pass_over(tmp_path, madeup_out):
    """T11, the variant of P8 whose second row is a week: the owner turns the test mode off while the route writes the first row, and
    the early check of the second row, a week that the button would pass over, stops the press and says why."""
    folder = []

    def test_mode_off(change):
        rewritten(folder[0] / "settings.json", "test_mode")
    route = StandIn(during=test_mode_off)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route)
    folder.append(tmp_path)
    listed = listed_by(page, "list_opened", "nl")
    clock.later()
    result = pressed(page, [value_of(listed, PRICE), value_of(listed, "week 48")])
    assert len(route.calls) == 1 and [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]
    assert result["note"] == " ".join([NL["hand_exact_written_one"], NL["hand_exact_not_made_one"], NL["hand_exact_stop:test_mode"]])


@pytest.mark.parametrize("what", ["dropped", "entered"])
def test_a_write_whose_newer_change_was_handled_during_the_call_says_its_row_was_not_ticked_off(tmp_path, madeup_out, what):
    """T13 (R14 of the second review round): the note "replaced" asks to enter or drop a newer change that stands open on the list.
    A newer row of the same line that the runner prepares during the call, and that is dropped (or ticked) at once, leaves none open:
    the note is the one of a write whose row was not ticked off."""
    folder = []

    def newer_and_handled(change):
        PreparedWriter(folder[0]).apply(WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50",
                                                new_value="12.90"), change.case_ids[0], "R04", {}, folder[1]())
        [newer] = [one for one in open_changes(folder[0]) if one.new_value == "12.90"]
        assert mark(folder[0], newer.ids, what, folder[1]()) > 0
    page, prepared, d, clock = route_page(tmp_path, madeup_out, StandIn(during=newer_and_handled), cases=("MU08",))
    folder.extend([prepared, clock])
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert [line["outcome"] for line in log_lines(prepared)] == [None, "verified"]
    states = {change.new_value: change.state for change in changes(prepared)[0] if change.kind == "po_line_price"}
    assert states == {"12.70": "replaced", "12.90": what}                   # the staging: the newer change is no longer open
    assert result["note"] == NL["hand_exact_unmarked_one"]


# --- the third review round of 17 November: the notes, the texts and the documents (the triage's T6 to T8) ---

@pytest.mark.parametrize("filed_at", ["before", "after", "equal"])
def test_what_a_print_shows_stands_beside_a_write_not_ticked_off_only_when_the_print_came_after_it(tmp_path, madeup_out, monkeypatch,
                                                                                                  filed_at):
    """T6: a row whose change the button put into Exact without ticking it off (UNTICKED) keeps the note of what Exact shows now
    ("Exact toont nu 12,80.") only when the store was given the print that shows it after the button's verified line. Version 2 of
    the order, which shows 12.80 for the line, filed before the press showed what Exact held before the aid's write, which the
    route's read back has since replaced: the row shows the button's note alone. Filed after the press, it shows what Exact holds
    now: the note stays, before the button's note. Filed at the very time of the verified line (the two times are equal on purpose:
    the boundary), it did not come after the write, and the row shows the button's note alone. In both languages."""
    from aid.review import page as page_module
    from aid.tests.fakes import line
    from aid.tests.test_by_hand import filed
    route = StandIn(outcome("verified", "read_back", "12.80" if filed_at == "before" else "12.50"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)

    def version_2(at: datetime) -> None:
        filed(d.store, 2, line("KM-0801", "3", "12.80", date="2026-11-16", basis="1"), line("KM-0802", "50", "0.42", date="2026-11-16", basis="1"),
              po="250108", at=at)
    clock.later()
    if filed_at == "before":
        version_2(clock())
        clock.later()
        assert f"{PRICE_ROW}. {NL['hand_exact_shows'].format(value='12,80')}" in texts_of(listed_by(page, "list_opened", "nl"))

    def held(*args, **kwargs):
        raise PermissionError("made-up: another program holds marks.jsonl")
    monkeypatch.setattr(page_module, "mark", held)
    assert pressed(page, [price])["note"] == NL["hand_exact_unmarked_one"]
    monkeypatch.undo()
    clock.later()
    if filed_at == "after":
        version_2(clock())
        clock.later()
    elif filed_at == "equal":
        [verified] = [one for one in log_lines(prepared) if one["outcome"] == "verified"]
        version_2(datetime.fromisoformat(verified["at"]))
        assert d.store.given_at("250108", "v2").timestamp() == datetime.fromisoformat(verified["at"]).timestamp()      # the staging
        clock.later()
    for index, (language, row, shown) in enumerate((("nl", PRICE_ROW, "12,80"), ("en", PRICE_ROW_EN, "12.80"))):
        unticked = nl_en("hand_exact_unticked_row")[index]
        noted = f"{row}. {unticked}" if filed_at != "after" else f"{row}. {STRINGS[language]['hand_exact_shows'].format(value=shown)} {unticked}"
        assert noted in texts_of(listed_by(page, "list_opened", language)), language


def test_the_documents_say_what_the_third_review_round_settled():
    """T8, and the documents of T1 to T6. The brief for a connector (the plug point's docstring and MANUAL 3.10) says that a route's
    target is fixed for the route's life and that write() reaches no other administration; the Dutch guide (MANUAL 7) and the README
    say that the assistant never puts a put-back into Exact; MANUAL 2.6 names a change that a newer document withdrew during the press
    among the rows not ticked off, and says when such a row names what a print showed; 3.10 says how the two checks of the gate are
    judged, that an entry id on two lines counts as an undo row and that a time the PC cannot place keeps a change held; the
    docstring of "Weer op de lijst zetten" (taken_back) says why its refusal of a put-back marked by "exact" stays; and the lines this
    branch rewrote in the README and in that docstring call the buyer "they"."""
    import ast

    from aid.execution import exact_route
    from aid.review import page as page_module
    gendered = re.compile(r"\b(?:h" r"e|h" r"im|h" r"is|h" r"imself|sh" r"e|h" r"er|h" r"ers|h" r"erself)\b", re.IGNORECASE)
    brief = " ".join(exact_route.__doc__.split())
    assert "A route's target is fixed for the route's life: set when route_for builds it, and never read again from a file or a setting." in brief
    assert "write() never reaches another administration than that target." in brief
    manual = manual_sections()
    three_ten = " ".join(manual["3.10"].split())
    for words in ("A route's target is fixed for the route's life: set when `route_for` builds it, and never read again from a file or a "
                  "setting; `write()` never reaches another administration than that target.",
                  "in both checks `available()` counts only when it returns `True`", "the last check asks it first",
                  "reading the settings file itself", "An entry id that stands on more than one line of the file counts as an undo row.",
                  "cannot place in its zone"):
        assert words in three_ten, words
    two_six = " ".join(manual["2.6"].split())
    for words in ("a newer document of the supplier, read during the press, withdrew the change",
                  "only when the print that shows that value came in after the aid's write"):
        assert words in two_six, words
    assert "Een terugzetting zet de assistent nooit zelf in Exact: die voert u zelf in." in " ".join(manual["7"].split())
    readme = Path("aid/README.md").read_text(encoding="utf-8")
    assert "it never puts a change back: the buyer enters every put-back" in " ".join(readme.split())
    rewritten_lines = [text for text in readme.splitlines() if text.startswith(("What the demo does not do", "One module per step of a case"))]
    [taken] = [node for node in ast.walk(ast.parse(Path(page_module.__file__).read_text(encoding="utf-8")))
               if isinstance(node, ast.FunctionDef) and node.name == "taken_back"]
    described = " ".join(ast.get_docstring(taken).split())
    assert "The button no longer makes the mark of a put-back" in described and "the refusal stays as a guard" in described
    found = {where: sorted(set(gendered.findall(text))) for where, text in (("README", "\n".join(rewritten_lines)), ("taken_back", described))
             if gendered.search(text)}
    assert len(rewritten_lines) == 2 and found == {}


# --- the check of the third review round: the last check's own judgment of available(), the order of its causes, and a time whose
# zone Windows cannot tell (the cases that its mutants a2, c2, b2 and f3 asked for are in the tests of T1, T3, T2 and T6 above) ---

class Answers(StandIn):
    """A stand-in whose available() gives the answers in turn, one for each call (the early check asks first, the last check second),
    and runs `meanwhile` with the number of the call inside each call, before it answers."""

    def __init__(self, *answers, meanwhile=None):
        super().__init__()
        self.answers, self.meanwhile, self.asked = list(answers), meanwhile, 0

    def available(self):
        self.asked += 1
        if self.meanwhile is not None:
            self.meanwhile(self.asked)
        return self.answers[self.asked - 1]


@pytest.mark.parametrize("answer", [False, "True"], ids=["False", "text_True"])
def test_a_route_that_is_not_available_at_the_last_check_is_not_written_to(tmp_path, madeup_out, answer):
    """The last check judges what available() answers it, and only True opens the gate: a route whose available() returns True at the
    early check and then, at its second call (the last check's), False, or the text "True" of a connector outside its contract, is
    not written to. Nothing is sent, logged or marked, and the note says that the connection is not available."""
    route = Answers(True, answer)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert route.asked == 2 and route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL['hand_exact_stop:unavailable']}"


@pytest.mark.parametrize("last, cause", [(False, "unavailable"), (True, "settings")])
def test_the_last_check_names_the_connection_first_when_the_settings_cannot_be_read_either(tmp_path, madeup_out, last, cause):
    """The order of the last check (T1) when two causes meet there: the settings file is cut off, its time moved later, during the
    early check's call of available(), and available() returns False at its second call, the last check's. The last check asks
    available() first, so the note names the connection, not the settings. The twin, whose route is still available at the last
    check, names the settings, which shows that both causes stand at that check. Nothing is sent, logged or marked."""
    def cut_off(call: int) -> None:
        if call == 1:
            rewritten(tmp_path / "settings.json", "no_json")
    route = Answers(True, last, meanwhile=cut_off)
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    result = pressed(page, [price])
    assert result["note"] == f"{NL['hand_exact_not_made_one']} {NL[f'hand_exact_stop:{cause}']}"
    assert route.asked == 2 and route.calls == [] and log_lines(prepared) == [] and mark_lines(prepared) == []


def zone_of_windows(monkeypatch) -> None:
    """exact_route._aware as it may behave on Windows, where astimezone() of a time without a zone before 1970 raises OSError (errno
    22) and Linux gives a time: forced by patching, so that it is the same on every system."""
    from aid.execution import exact_route
    real = exact_route._aware

    def aware(moment: datetime) -> datetime:
        if moment.utcoffset() is None and moment.year < 1970:
            raise OSError(22, "made-up: Invalid argument")
        return real(moment)
    monkeypatch.setattr(exact_route, "_aware", aware)


@pytest.mark.parametrize("kind, note", [("not_known", "hand_exact_unsure_row"), ("verified", "hand_exact_unticked_row")])
def test_a_line_of_the_routes_log_whose_time_raises_an_oserror_keeps_the_change_held(tmp_path, madeup_out, monkeypatch, kind, note):
    """On Windows, astimezone() of a time without a zone before 1970 can raise OSError. After a write in doubt, a line of the route's
    log with such a time (a clock set wrong, or an edit by hand) is the change's last: it keeps the change held as a line in the year
    1 or 9999 does (T4), a line in doubt as a write in doubt and a verified one as a write not ticked off, and the list keeps its
    notes. The list reads, the row keeps its note, and a press passes the row over without calling the route or logging a line."""
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_unsure_one"]
    with open(prepared / LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": "1969-12-31T23:00:00", "entry_ids": ids_of(price), "outcome": kind, "reason": "made_up", "before": None}) + "\n")
    zone_of_windows(monkeypatch)
    clock.later()
    listed = listed_by(page, "list_opened", "nl")
    assert listed["tab"]["label"] == NL["hand_tab_count"].format(n=2)          # the list reads, not "In Exact invoeren (?)"
    assert f"{PRICE_ROW}. {NL[note]}" in texts_of(listed)
    lines = log_lines(prepared)
    clock.later()
    again = pressed(page, [price])
    assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"] and log_lines(prepared) == lines


@pytest.mark.parametrize("ticked_later", [False, True], ids=["alone", "a_later_tick_settles"])
def test_a_mark_whose_time_raises_an_oserror_settles_nothing(tmp_path, madeup_out, monkeypatch, ticked_later):
    """A mark "entered" or "seen" settles a write in doubt by its time, and a mark whose time the clock of this PC cannot place in its
    zone is passed over, as one in the year 1 or 9999 is: on Windows, astimezone() of a time without a zone before 1970 can raise
    OSError. After a write in doubt the row was ticked with such a time (a clock set wrong, or an edit by hand) and the tick taken
    back (by_hand.reopen, the work of "Weer op de lijst zetten"), so that the row stands on the list again and the change's last
    line of the log is the write in doubt, whose time can be placed. That tick settles nothing, and the list keeps its notes: the
    row keeps its note, and a press passes the row over without calling the route or logging a line. The twin: the buyer then ticks
    the row at a time that can be placed, and a print ends that tick. Only the mark that cannot be placed is passed over, so the
    later tick settles the write in doubt: the row has the print's note and no note of the button, and a press puts it into Exact."""
    from aid.execution.by_hand import reopen
    route = StandIn(outcome("not_known", "no_answer"))
    page, prepared, d, clock = route_page(tmp_path, madeup_out, route, cases=("MU08",))
    price = value_of(listed_by(page, "list_opened", "nl"), PRICE)
    clock.later()
    assert pressed(page, [price])["note"] == NL["hand_exact_unsure_one"]
    with open(prepared / MARKS_FILE, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps({"entry_id": entry_id, "mark": "entered", "at": "1969-12-31T23:00:00", "by": "buyer"}) + "\n"
                        for entry_id in ids_of(price)))
    clock.later()
    assert reopen(prepared, ids_of(price), clock()) == "open"                # the staging: the row is on the list again
    if ticked_later:
        clock.later()
        assert mark(prepared, ids_of(price), "entered", clock()) == len(ids_of(price))
        clock.later()
        ended_by_print(prepared, ids_of(price), clock(), "12.50")
    zone_of_windows(monkeypatch)
    clock.later()
    listed = listed_by(page, "list_opened", "nl")
    assert listed["tab"]["label"] == NL["hand_tab_count"].format(n=2)          # the list reads, not "In Exact invoeren (?)"
    lines = log_lines(prepared)
    clock.later()
    if not ticked_later:
        assert f"{PRICE_ROW}. {NL['hand_exact_unsure_row']}" in texts_of(listed)
        again = pressed(page, [price])
        assert len(route.calls) == 1 and again["note"] == NL["hand_exact_passed_one"] and log_lines(prepared) == lines
    else:
        assert f"{PRICE_ROW}. {NL['hand_tick_ended'].format(value='12,50')}" in texts_of(listed)
        again = pressed(page, [price])
        assert len(route.calls) == 2 and again["note"] == NL["hand_exact_written_one"]
        assert [line["outcome"] for line in log_lines(prepared)[len(lines):]] == [None, "verified"]
