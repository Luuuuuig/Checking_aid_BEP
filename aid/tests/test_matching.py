"""Tests of line matching (aid/matching.py): the rules on small made-up documents with fake deciders, random documents with
random deciders, and the whole made-up set against its references. No test calls a model."""
import csv
import random
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from aid.decider import MIN_CONFIDENCE, Decision, DecisionAnswer, NoDecider
from aid.madeup.generate import EXPORT_COLUMNS
from aid.matching import HELD_CONFIDENCE, SIM_CANDIDATE, SIM_MATCH, MatchResult, Pair, match_lines, norm_code
from aid.po_store import PoStore
from aid.readers.po_pdf import read_po_pdf
from aid.readers.supplier_parser import detect_template, load_templates, read_with_template
from aid.schema import DocLine, DocValue, Document
from aid.tests.fakes import FakeDecider, confirmation, line, po
from aid.tests.helpers import document_from_spec

with open(Path(__file__).resolve().parents[1] / "madeup" / "references.csv", encoding="utf-8", newline="") as _f:
    PRESENCE = [row for row in csv.DictReader(_f) if row["item_type"] == "line presence"]
CASES = list(dict.fromkeys(row["case_id"] for row in PRESENCE))         # MU01 to MU20, then the carts
EXPORT_MAP = {field: field for field in EXPORT_COLUMNS}                  # the made-up export names its columns after the aid's fields


# --- the brief's tests ---

def test_code_match_and_extra():
    r = match_lines(confirmation([line("KM-1", "5"), line("KM-9", "1")]), po([line("KM-1", "5"), line("KM-2", "2")]), NoDecider())
    assert r.pairs[0].basis == "code" and r.unmatched_po == ["2"] and r.extra_doc == ["2"]

def test_merged_line(madeup_out):
    doc = document_from_spec("MU19"); po_doc = read_po_pdf(madeup_out / "MU19_po_v1.pdf")
    r = match_lines(doc, po_doc, NoDecider()); assert any(p.kind == "merged" and len(p.po_line_ids) == 2 for p in r.pairs)

def test_description_similarity_and_uncertain():
    # The brief's candidate "Hose 10 mm" has a similarity of 2 x 10 / 35 = 0.57 to the PO's description, below SIM_CANDIDATE,
    # so it would be an extra line; "Hose 10 mm, 2 m" (2 x 15 / 40 = 0.75) is the candidate the test means.
    po_doc = po([line("", "5", desc="Hydraulic hose 10 mm, 2 m")])
    r = match_lines(confirmation([line("", "5", desc="Hydraulic hose 10mm 2m")]), po_doc, NoDecider())
    assert r.pairs and r.pairs[0].basis == "description"
    r2 = match_lines(confirmation([line("", "5", desc="Hose 10 mm, 2 m")]), po_doc, NoDecider())
    assert r2.uncertain and not r2.pairs and r2.unmatched_po == [] and r2.extra_doc == []
    r3 = match_lines(confirmation([line("", "5", desc="Hose 10 mm, 2 m")]), po_doc, FakeDecider("1", 0.95))
    assert r3.pairs[0].basis == "model"


# The tests below cover what the rulings of 29 October add to the brief's tests above, and those of the review of 30 October.

def pair(po_ids: list[str], doc_ids: list[str], basis: str, confidence: float, kind: str = "matched") -> Pair:
    return Pair(po_line_ids=po_ids, doc_line_ids=doc_ids, basis=basis, confidence=confidence, kind=kind)


def reversed_lines(document: Document) -> Document:
    """The document with its lines in the opposite order, each keeping its line id."""
    return document.model_copy(update={"lines": document.lines[::-1]})


class ScriptedDecider:
    """Answers each document line by its description from the answers it holds, and any other line with no choice."""
    name = "scripted"

    def __init__(self, answers: dict[str, tuple[str | None, float]]):
        self.answers, self.decisions = answers, []

    def decide(self, d: Decision) -> DecisionAnswer:
        self.decisions.append(d)
        choice, confidence = self.answers.get(d.context["document_line"]["description"], (None, 0.0))
        return DecisionAnswer(choice=choice, confidence=confidence, rationale="scripted", source=self.name)


HOSE = "Hydraulic hose 10 mm, 2 m"
SHORT_HOSE = "Hose 10 mm, 2 m"                    # 0.75 = 2 x 15 / 40 against HOSE: a candidate
VALVE = "Ball valve DN15"


# --- (1) codes ---

def test_norm_code_keeps_upper_case_letters_and_digits():
    assert norm_code("km-1901") == "KM1901" and norm_code(" KM 1901/a ") == "KM1901A"
    assert norm_code(None) is None and norm_code("") is None and norm_code(" - / ") is None


def test_codes_match_whatever_their_case_and_punctuation_and_the_text_stands_in_for_a_missing_value():
    po_doc = po([line("KM-1", "5")])
    assert match_lines(confirmation([line("km 1", "5")]), po_doc, NoDecider()).pairs == [pair(["1"], ["1"], "code", 1.0)]
    doc = confirmation([line("", "5")])
    doc.lines[0].item_code = DocValue(text="km 1")                                 # not read as a value: the text stands in
    r = match_lines(doc, po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1"], ["1"], "code", 1.0)], [], [], [])
    po_doc.lines[0].item_code = DocValue(text="KM-1", uncertain=True)              # the same on the PO's side
    assert match_lines(confirmation([line("KM-1", "5")]), po_doc, NoDecider()).pairs == [pair(["1"], ["1"], "code", 1.0)]


def test_several_document_lines_with_one_po_code_are_one_split_pair():
    r = match_lines(confirmation([line("KM-1", "6"), line("KM-2", "3"), line("KM-1", "4")]), po([line("KM-1", "10"), line("KM-2", "3")]), NoDecider())
    assert r.pairs == [pair(["1"], ["1", "3"], "code", 1.0, "split"), pair(["2"], ["2"], "code", 1.0)]
    assert (r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [])


def test_the_merged_line_of_mu19_through_the_real_po_pdf(madeup_out):
    po_doc = read_po_pdf(madeup_out / "MU19_po_v1.pdf")
    expected = [pair(["1", "2"], ["1"], "code", 1.0, "merged"), pair(["3"], ["2"], "code", 1.0)]
    r = match_lines(document_from_spec("MU19"), po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == (expected, [], [], [])
    parsed = read_with_template(madeup_out / "MU19_conf.pdf", detect_template(madeup_out / "MU19_conf.pdf", load_templates()))
    assert parsed.lines[0].item_code.uncertain                          # the parser marks the line with two codes uncertain ...
    assert match_lines(parsed, po_doc, NoDecider()).pairs == expected   # ... which does not change the matching


def test_a_lines_own_code_wins_over_the_po_codes_its_texts_hold():
    po_doc = po([line("KM-1", "1"), line("KM-2", "1"), line("KM-3", "1")])
    r = match_lines(confirmation([line("KM-3", "1", desc="Seal kit for KM-1 and KM-2")]), po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["3"], ["1"], "code", 1.0)], ["1", "2"], [], [])
    r = match_lines(confirmation([line("KM-1", "1", desc="Valve body, fits seal kit KM-2")]), po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po) == ([pair(["1"], ["1"], "code", 1.0)], ["2", "3"])


def test_an_item_code_that_holds_two_or_more_po_codes_is_a_merged_line():
    po_doc = po([line("KM-1", "4"), line("KM-2", "4"), line("KM-10", "1")])
    r = match_lines(confirmation([line("km-1/km-2", "4"), line("KM-10", "1")]), po_doc, NoDecider())       # KM-10 holds no KM-1
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([pair(["1", "2"], ["1"], "code", 1.0, "merged"), pair(["3"], ["2"], "code", 1.0)], [], [])
    r = match_lines(confirmation([line("XKM-1 / XKM-2", "4")]), po_doc, NoDecider())                       # other codes that end in them
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], ["1", "2", "3"], ["1"], [])
    r = match_lines(confirmation([line("KM-1 / KM-2", "4", desc="Valve body DN25, fits KM-10")]), po_doc, NoDecider())   # the item code first
    assert (r.pairs, r.unmatched_po, r.uncertain) == ([pair(["1", "2"], ["1"], "code", 1.0, "merged")], ["3"], [])
    r = match_lines(confirmation([line("A-1 / B-2", "4")]), po([line("A-1", "4"), line("B-2", "4")]), NoDecider())   # short codes too
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1", "2"], ["1"], "code", 1.0, "merged")], [], [], [])


def test_a_description_that_echoes_a_po_line_is_matched_by_description_before_its_held_codes_count():
    po_doc = po([line("KM-1", "1", desc="Valve body DN25"), line("KM-2", "1", desc="Gasket DN25"), line("KM-3", "1", desc="Seal kit for KM-1 and KM-2")])
    r = match_lines(confirmation([line("S-7", "1", desc="Seal kit for KM-1 and KM-2")]), po_doc, NoDecider())  # in the supplier's words
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["3"], ["1"], "description", 1.0)], ["1", "2"], [], [])
    echo = po([line("KM-1901", "1", desc="Valve KM-1901 DN25")])
    r = match_lines(confirmation([line("S-1", "1", desc="Valve KM-1901 DN25")]), echo, NoDecider())        # its held code agrees
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1"], ["1"], "description", 1.0)], [], [], [])


def test_a_held_code_that_points_away_from_the_description_leaves_the_line_uncertain():
    po_doc = po([line("KM-1", "1", desc="Valve DN25"), line("KM-2", "1", desc="Gasket DN25")])
    doc = confirmation([line("KM-1-B", "1", desc="Gasket DN25")])                                          # KM-1 by code, KM-2 by description
    for decider in (NoDecider(), FakeDecider("2", 0.99)):
        r = match_lines(doc, po_doc, decider)
        assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [], [pair(["1", "2"], ["1"], "description", 1.0)])
    taken = match_lines(confirmation([line("KM-1-B", "1", desc="Gasket DN25"), line("KM-1", "1")]), po_doc, NoDecider())   # KM-1 is answered,
    assert (taken.pairs, taken.uncertain) == ([pair(["1"], ["2"], "code", 1.0)], [pair(["2"], ["1"], "description", 1.0)])  # but still stated
    three = po([line("KM-1", "1", desc="Valve DN25"), line("KM-2", "1", desc="Gasket DN25"), line("KM-3", "1", desc="Gasket DN40")])
    both = match_lines(confirmation([line("KM-1-B", "1", desc="Gasket DN25"), line("KM-1-C", "1", desc="Gasket DN40")]), three, NoDecider())
    assert (both.pairs, both.uncertain) == ([], [pair(["1", "2"], ["1"], "description", 1.0), pair(["1", "3"], ["2"], "description", 1.0)])   # each


def test_a_held_code_that_the_matched_po_line_describes_as_well_is_an_echo_and_no_conflict():
    po_doc = po([line("KM-1901", "1", desc="Klephuis DN25"), line("KM-1902", "1", desc="Afdichtingsset voor KM-1901")])
    doc = confirmation([line("S-8", "1", desc="Afdichtingsset voor KM-1901")])                             # the supplier echoes PO line 2
    r = match_lines(doc, po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["2"], ["1"], "description", 1.0)], ["1"], [], [])
    other = po([line("KM-1901", "1", desc="Klephuis DN25"), line("KM-1902", "1", desc="Afdichtingsset voor KM-1910")])   # without KM-1901
    r = match_lines(doc, other, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [], [pair(["1", "2"], ["1"], "description", 26 / 27)])
    two = po([line("KM-1", "1", desc="Valve body DN25"), line("KM-2", "1", desc="Gasket DN25"), line("KM-3", "1", desc="Seal kit for KM-1 and KM-4")])
    r = match_lines(confirmation([line("S-7", "1", desc="Seal kit for KM-1 and KM-2")]), two, NoDecider())   # KM-1 is echoed, KM-2 is not
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], ["1"], [], [pair(["2", "3"], ["1"], "description", 50 / 52)])
    twice = po([line("KM-1901", "1", desc="Klephuis DN25 met afdichtingsset en bouten"), line("KM-1901", "1", desc="Klephuis DN40")])
    r = match_lines(confirmation([line("S-1", "1", desc="Klephuis DN25 met afdichtingsset en bouten KM-1901")]), twice, NoDecider())
    assert (r.pairs, r.unmatched_po, r.uncertain) == ([pair(["1"], ["1"], "description", 21 / 23)], ["2"], [])   # the matched line's own code


def test_one_po_code_held_in_a_text_is_a_candidate_for_the_decider_and_never_a_match():
    one = po([line("KM-1901", "4", desc="Klephuis DN25")])
    for doc, found_in in [(confirmation([line("S-55", "4", desc="Klephuis DN25, uw artikelnummer KM-1901")]), "description"),   # the supplier's code
                          (confirmation([line("KM-1901 / KM-1902", "4")]), "item_code")]:                                        # KM-1902 is no PO code
        r = match_lines(doc, one, NoDecider())
        assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [], [pair(["1"], ["1"], "code", HELD_CONFIDENCE)])
        decider = FakeDecider("1", 0.9)
        assert match_lines(doc, one, decider).pairs == [pair(["1"], ["1"], "model", 0.9)]
        [decision] = decider.decisions
        assert (decision.options, decision.context["held_code"], decision.context["found_in"]) == (["1"], "KM-1901", found_in)


def test_po_codes_held_only_in_the_description_are_an_uncertain_merged_pair_without_a_question():
    decider = FakeDecider("1", 0.99)
    po_doc = po([line("KM-1", "4"), line("KM-2", "4"), line("KM-10", "1")])
    r = match_lines(confirmation([line("S-200", "4", desc="Valve body with seal kit km-1/km-2")]), po_doc, decider)
    assert (r.pairs, r.unmatched_po, r.extra_doc, decider.decisions) == ([], ["3"], [], [])
    assert r.uncertain == [pair(["1", "2"], ["1"], "code", HELD_CONFIDENCE, "merged")]


def test_a_variant_of_a_po_code_is_a_candidate_and_a_short_po_code_is_not_looked_for():
    for po_code, doc_code in [("6205", "6205-2RS"), ("KM-1", "KM-1-B")]:                                    # another article, maybe
        po_doc, doc = po([line(po_code, "2")]), confirmation([line(doc_code, "2")])
        r = match_lines(doc, po_doc, NoDecider())
        assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [], [pair(["1"], ["1"], "code", HELD_CONFIDENCE)])
        assert match_lines(doc, po_doc, FakeDecider("1", 0.9)).pairs == [pair(["1"], ["1"], "model", 0.9)]
    decider = FakeDecider("1", 0.99)
    r = match_lines(confirmation([line("S-4", "2", desc="Hose 10 mm")]), po([line("10", "2")]), decider)   # "10" is not looked for
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain, decider.decisions) == ([], ["1"], ["1"], [], [])


def test_a_po_code_inside_a_longer_po_code_is_not_held():
    po_doc = po([line("6205", "2"), line("6205-2RS", "2")])
    r = match_lines(confirmation([line("6205-2RS", "2")]), po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["2"], ["1"], "code", 1.0)], ["1"], [], [])
    r = match_lines(confirmation([line("6205", "2"), line("6205-2RS", "2")]), po_doc, NoDecider())
    assert (r.pairs, r.uncertain) == ([pair(["1"], ["1"], "code", 1.0), pair(["2"], ["2"], "code", 1.0)], [])
    r = match_lines(confirmation([line("S-9", "2", desc="Bearing, uw artikelnummer 6205-2RS")]), po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.uncertain) == ([], ["1"], [pair(["2"], ["1"], "code", HELD_CONFIDENCE)])   # a candidate of 6205-2RS only
    three = po([line("6205", "2"), line("6205-2RS", "2"), line("6206", "2")])
    r = match_lines(confirmation([line("6205-2RS / 6206", "2")]), three, NoDecider())
    assert (r.pairs, r.unmatched_po) == ([pair(["2", "3"], ["1"], "code", 1.0, "merged")], ["1"])


def test_a_merged_line_that_codes_do_not_settle_is_uncertain():
    r = match_lines(confirmation([line("KM-1 / KM-2", "4"), line("KM-2", "1")]), po([line("KM-1", "4"), line("KM-2", "4")]), NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([], [], [])
    assert r.uncertain == [pair(["1", "2"], ["1", "2"], "code", 1.0, "merged")]          # PO line 2 is claimed twice
    r = match_lines(confirmation([line("KM-1 / KM-2", "4")]), po([line("KM-1", "5"), line("KM-1", "3"), line("KM-2", "4")]), NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([], [], [])
    assert r.uncertain == [pair(["1", "2", "3"], ["1"], "code", 1.0, "merged")]         # KM-1 is on two PO lines


def test_a_code_on_several_po_lines_is_paired_by_quantity():
    decider = FakeDecider("1", 0.99)
    doc = confirmation([line("KM-1", "3"), line("KM-2", "1"), line("KM-1", "5.00"), line("KM-1", "7")])      # 5.00 is the quantity 5
    r = match_lines(doc, po([line("KM-1", "5"), line("KM-2", "1"), line("KM-1", "3")]), decider)
    assert r.pairs == [pair(["1"], ["3"], "code", 1.0), pair(["2"], ["2"], "code", 1.0), pair(["3"], ["1"], "code", 1.0)]   # in PO order
    assert (r.unmatched_po, r.extra_doc, r.uncertain, decider.decisions) == ([], ["4"], [], [])    # no PO line with KM-1 is left for line 4


def test_a_code_on_several_po_lines_that_the_quantity_does_not_settle_goes_to_the_decider_then_to_uncertain():
    po_doc = po([line("KM-1", "5", desc="Hose clamp 20-32 mm"), line("KM-1", "3", desc="Hose clamp 12-20 mm")])
    doc = confirmation([line("KM-1", "4", desc="Hose clamp 12-20 mm")])                                   # neither quantity
    r = match_lines(doc, po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([], [], [])
    assert r.uncertain == [pair(["1", "2"], ["1"], "code", 1.0)]
    decider = FakeDecider("2", 0.9)
    r = match_lines(doc, po_doc, decider)
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["2"], ["1"], "model", 0.9)], ["1"], [], [])
    [decision] = decider.decisions
    assert (decision.kind, decision.options) == ("line_match", ["1", "2"])
    assert decision.context == {
        "document_line": {"item_code": "KM-1", "description": "Hose clamp 12-20 mm", "quantity": "4"},
        "candidates": [{"line_id": "1", "item_code": "KM-1", "description": "Hose clamp 20-32 mm", "quantity": "5", "ratio": 16 / 19},
                       {"line_id": "2", "item_code": "KM-1", "description": "Hose clamp 12-20 mm", "quantity": "3", "ratio": 1.0}]}
    twice = match_lines(confirmation([line("KM-1", "5")]), po([line("KM-1", "5"), line("KM-1", "5")]), NoDecider())   # the quantity of both
    assert (twice.pairs, twice.uncertain) == ([], [pair(["1", "2"], ["1"], "code", 1.0)])
    both = match_lines(confirmation([line("KM-1", "5"), line("KM-1", "5")]), po([line("KM-1", "5"), line("KM-1", "3")]), NoDecider())
    assert (both.pairs, both.uncertain) == ([], [pair(["1", "2"], ["1"], "code", 1.0), pair(["1", "2"], ["2"], "code", 1.0)])


def test_the_lines_that_codes_leave_to_the_decider_are_not_matched_by_description():
    po_doc = po([line("KM-1", "5", desc="Hose clamp 20-32 mm"), line("KM-1", "3", desc="Hose clamp 12-20 mm"), line("KM-3", "3", desc="Hose clamp 12-20 mm")])
    doc = confirmation([line("KM-1", "4", desc="Hose clamp 12-20 mm"), line("", "3", desc="Hose clamp 12-20 mm")])
    r = match_lines(doc, po_doc, NoDecider())       # line 1 waits for the decider with PO lines 1 and 2, so line 2 has only PO line 3 to match
    assert (r.pairs, r.uncertain) == ([pair(["3"], ["2"], "description", 1.0)], [pair(["1", "2"], ["1"], "code", 1.0)])
    assert (r.unmatched_po, r.extra_doc) == ([], [])
    alone = match_lines(confirmation(doc.lines[:1]), po_doc, NoDecider())         # nor is the waiting line itself matched to PO line 3
    assert (alone.pairs, alone.uncertain, alone.unmatched_po) == ([], [pair(["1", "2"], ["1"], "code", 1.0)], ["3"])


def test_a_document_line_whose_own_code_is_uncertain_makes_no_pair_by_code():
    """Final review, finding 10: an item code the reader marks uncertain is a code nobody could verify, so it never makes a
    pair by code, not even with a description that is the PO's own: the decider is asked, and without a choice the line is
    an uncertain pair, basis "code" with HELD_CONFIDENCE. The other lines are matched as before."""
    po_doc = po([line("KM-1", "5", desc=HOSE), line("KM-2", "3", desc=VALVE)])
    doc = confirmation([line("KM-1", "5", desc=HOSE), line("KM-2", "3", desc=VALVE)])
    doc.lines[0].item_code = DocValue(value="KM-1", text="KM-1", uncertain=True)
    decider = FakeDecider(None, 0.0)
    r = match_lines(doc, po_doc, decider)
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([pair(["2"], ["2"], "code", 1.0)], [], [])
    assert r.uncertain == [pair(["1"], ["1"], "code", HELD_CONFIDENCE)]
    assert [d.options for d in decider.decisions] == [["1"]]
    r = match_lines(doc, po_doc, FakeDecider("1", 0.9))                            # the decider's choice is a pair by the model
    assert (r.pairs, r.uncertain) == ([pair(["2"], ["2"], "code", 1.0), pair(["1"], ["1"], "model", 0.9)], [])
    doc.lines[0].item_code = DocValue(text="km 1", uncertain=True)                 # not read as a value, and uncertain
    assert match_lines(doc, po_doc, NoDecider()).uncertain == [pair(["1"], ["1"], "code", HELD_CONFIDENCE)]
    taken = confirmation([line("KM-1", "5", desc=HOSE), line("KM-1", "5", desc="Hydraulic hose 10 mm, 3 m")])
    taken.lines[1].item_code = DocValue(value="KM-1", text="KM-1", uncertain=True)  # its PO line is paired by a code that is certain
    r = match_lines(taken, po([line("KM-1", "5", desc=HOSE)]), NoDecider())
    assert (r.pairs, r.uncertain, r.extra_doc) == ([pair(["1"], ["1"], "code", 1.0)], [], ["2"])


# --- (2) descriptions ---

def test_description_pairs_do_not_depend_on_the_order_of_the_lines():
    po_doc = po([line("", "1", desc="Ball valve DN15 brass"), line("", "1", desc="Ball valve DN25 brass")])
    doc = confirmation([line("", "1", desc="Ball valve DN15, brass"), line("", "1", desc="Ball valve DN15 brass")])
    # Document line 2 is PO line 1 exactly (1.0), and document line 1 is a little less like it (42 / 43). Taking the
    # document's lines in their order would give PO line 1 to document line 1 and PO line 2 to document line 2 (20 / 21);
    # the most similar pair first gives PO line 1 to document line 2 and PO line 2 to document line 1 (40 / 43).
    expected = [pair(["1"], ["2"], "description", 1.0), pair(["2"], ["1"], "description", 40 / 43)]
    for d in (doc, reversed_lines(doc)):
        for p in (po_doc, reversed_lines(po_doc)):
            r = match_lines(d, p, NoDecider())
            assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == (expected, [], [], [])


def test_lines_that_tie_on_their_description_are_paired_by_quantity_else_left_to_the_decider():
    po_doc = po([line("KM-1", "5", desc=VALVE), line("KM-1", "3", desc=VALVE)])                            # one article on two lines
    doc = confirmation([line("S-1", "3", desc=VALVE), line("S-1", "5", desc=VALVE)])                       # under the supplier's code
    for d in (doc, reversed_lines(doc)):
        r = match_lines(d, po_doc, NoDecider())
        assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1"], ["2"], "description", 1.0), pair(["2"], ["1"], "description", 1.0)],
                                                                       [], [], [])
    r = match_lines(confirmation([line("S-1", "4", desc=VALVE)]), po_doc, NoDecider())                    # neither quantity
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [], [pair(["1", "2"], ["1"], "description", 1.0)])
    assert match_lines(confirmation([line("S-1", "4", desc=VALVE)]), po_doc, FakeDecider("2", 0.9)).pairs == [pair(["2"], ["1"], "model", 0.9)]
    r = match_lines(confirmation([line("S-1", "5", desc=VALVE), line("S-1", "3", desc=VALVE)]), po([line("KM-1", "5", desc=VALVE)]), NoDecider())
    assert (r.pairs, r.extra_doc, r.uncertain) == ([pair(["1"], ["1"], "description", 1.0)], ["2"], [])    # a tie on the PO line's side
    chain = match_lines(confirmation([line("", "3", desc="Valve DN1x"), line("", "7", desc="Valve DM16")]),          # 0.9 three times:
                        po([line("", "5", desc="Valve DN15"), line("", "3", desc="Valve DN16")]), NoDecider())     # 1-1, 1-2 and 2-2
    assert (chain.pairs, chain.uncertain) == ([pair(["2"], ["1"], "description", SIM_MATCH)], [pair(["1"], ["2"], "description", 0.8)])


def test_each_threshold_belongs_to_the_band_above_it():
    r = match_lines(confirmation([line("", "1", desc="Valve DN16")]), po([line("", "1", desc="Valve DN15")]), NoDecider())   # 2 x 9 / 20
    assert r.pairs == [pair(["1"], ["1"], "description", SIM_MATCH)]
    r = match_lines(confirmation([line("", "1", desc="Valve body")]), po([line("", "1", desc="Valve seat")]), NoDecider())   # 2 x 6 / 20
    assert (r.pairs, r.uncertain) == ([], [pair(["1"], ["1"], "description", SIM_CANDIDATE)])


def test_a_line_with_several_candidates_left_uncertain_has_their_best_similarity():
    decider = FakeDecider(None, 0.0)
    r = match_lines(confirmation([line("", "5", desc=SHORT_HOSE)]), po([line("", "5", desc=HOSE), line("", "5", desc="Hydraulic hose 10 mm, 3 m")]), decider)
    assert [d.options for d in decider.decisions] == [["1", "2"]]
    assert (r.pairs, r.uncertain) == ([], [pair(["1", "2"], ["1"], "description", 0.75)])             # 0.75 against 0.7


def test_a_pair_by_description_or_by_the_model_whose_line_states_the_code_of_another_po_line_is_uncertain():
    """Final review, finding 11: a document line whose item code states the code of another PO line (KM-1902, taken by code by
    another line) points one of the PO's codes elsewhere, so a pair by description or by the model with PO line KM-1901 is
    uncertain, even at a similarity of 0.96 ("2 m" against "3 m"). The supplier's own code, an item code that holds the paired
    line's code and a line without a code are paired as before."""
    three_m = "Hydraulic hose 10 mm, 3 m"
    ratio = SequenceMatcher(None, three_m.lower(), HOSE.lower()).ratio()
    assert ratio >= SIM_MATCH
    po_doc = po([line("KM-1901", "5", desc=HOSE), line("KM-1902", "2", desc=VALVE)])
    valve = line("KM-1902", "2", desc=VALVE)                                       # takes PO line 2 by code
    r = match_lines(confirmation([line("KM-1902/B", "5", desc=three_m), valve]), po_doc, NoDecider())
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([pair(["2"], ["2"], "code", 1.0)], [], [])
    assert r.uncertain == [pair(["1"], ["1"], "description", ratio)]
    r = match_lines(confirmation([line("KM-1902/B", "5", desc=SHORT_HOSE), valve]), po_doc, FakeDecider("1", 0.95))
    assert (r.pairs, r.uncertain) == ([pair(["2"], ["2"], "code", 1.0)], [pair(["1"], ["1"], "model", 0.95)])
    split = match_lines(confirmation([line("", "2", desc=SHORT_HOSE), line("KM-1902/B", "3", desc=SHORT_HOSE), valve]), po_doc,
                        FakeDecider("1", 0.9))
    assert (split.pairs, split.uncertain) == ([pair(["2"], ["3"], "code", 1.0)], [pair(["1"], ["1"], "model", 0.9), pair(["1"], ["2"], "model", 0.9)])
    for code in ("", "XX-77", "KM-1901 alt"):                                      # none, the supplier's, and one that holds KM-1901
        r = match_lines(confirmation([line(code, "5", desc=three_m), valve]), po_doc, NoDecider())
        assert (r.pairs, r.uncertain) == ([pair(["2"], ["2"], "code", 1.0), pair(["1"], ["1"], "description", ratio)], []), code


# --- (3) the decider ---

def test_the_decider_sees_the_document_line_and_its_candidates():
    decider = FakeDecider("1", 0.9)
    r = match_lines(confirmation([line("S-7", "5", desc=SHORT_HOSE)]), po([line("KM-7", "4", desc=HOSE)]), decider)
    assert r.pairs == [pair(["1"], ["1"], "model", 0.9)]
    [decision] = decider.decisions
    assert (decision.kind, decision.options) == ("line_match", ["1"])
    assert decision.context == {"document_line": {"item_code": "S-7", "description": SHORT_HOSE, "quantity": "5"},
                                "candidates": [{"line_id": "1", "item_code": "KM-7", "description": HOSE, "quantity": "4", "ratio": 0.75}]}


def test_the_decider_matches_only_with_one_of_the_options_and_enough_confidence():
    po_doc, doc = po([line("", "5", desc=HOSE)]), confirmation([line("", "5", desc=SHORT_HOSE)])
    assert match_lines(doc, po_doc, FakeDecider("1", MIN_CONFIDENCE)).pairs == [pair(["1"], ["1"], "model", MIN_CONFIDENCE)]
    for decider, left in [(FakeDecider("1", 0.79), pair(["1"], ["1"], "model", 0.79)),          # below the threshold
                          (FakeDecider("2", 0.95), pair(["1"], ["1"], "description", 0.75)),    # not one of the options
                          (FakeDecider(None, 0.95), pair(["1"], ["1"], "description", 0.75))]:  # no choice
        r = match_lines(doc, po_doc, decider)
        assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([], [], [], [left])


def test_a_po_line_stays_a_candidate_of_every_document_line_and_the_decider_is_asked_once_per_line():
    decider = FakeDecider(None, 0.0)
    r = match_lines(confirmation([line("", "1", desc=SHORT_HOSE), line("", "1", desc="Hydraulic hose")]), po([line("", "1", desc=HOSE)]), decider)
    assert [(d.options, d.context["document_line"]["description"]) for d in decider.decisions] == [(["1"], SHORT_HOSE), (["1"], "Hydraulic hose")]
    assert r.uncertain == [pair(["1"], ["1"], "description", 0.75), pair(["1"], ["2"], "description", 28 / 39)]
    assert (r.pairs, r.unmatched_po, r.extra_doc) == ([], [], [])
    taken = match_lines(confirmation([line("", "1", desc="Hydraulic hose 10mm 2m"), line("", "1", desc=SHORT_HOSE)]), po([line("", "1", desc=HOSE)]),
                        NoDecider())
    assert (taken.pairs, taken.uncertain, taken.extra_doc) == ([pair(["1"], ["1"], "description", 44 / 47)], [], ["2"])   # a PO line in a pair is no candidate


def test_the_lines_that_choose_one_po_line_are_one_split_pair_with_their_lowest_confidence():
    decider = ScriptedDecider({SHORT_HOSE: ("1", 0.9), "Hydraulic hose": ("1", 0.85)})
    r = match_lines(confirmation([line("", "1", desc=SHORT_HOSE), line("", "1", desc="Hydraulic hose")]), po([line("", "2", desc=HOSE)]), decider)
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1"], ["1", "2"], "model", 0.85, "split")], [], [], [])
    assert len(decider.decisions) == 2


def test_a_po_line_chosen_for_one_line_is_no_candidate_of_the_others():
    doc = confirmation([line("", "1", desc=SHORT_HOSE), line("", "1", desc="Hydraulic hose")])
    r = match_lines(doc, po([line("", "1", desc=HOSE)]), ScriptedDecider({SHORT_HOSE: ("1", 0.9)}))
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1"], ["1"], "model", 0.9)], [], ["2"], [])   # no candidate is left
    two = po([line("", "1", desc=HOSE), line("", "1", desc="Hydraulic hose 10 mm, 3 m")])                 # line 2 is 28 / 39 like either
    for answer, left in [(("1", 0.5), pair(["2"], ["2"], "description", 28 / 39)),                       # its choice went to line 1
                         (("2", 0.5), pair(["2"], ["2"], "model", 0.5))]:
        r = match_lines(doc, two, ScriptedDecider({SHORT_HOSE: ("1", 0.9), "Hydraulic hose": answer}))
        assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain) == ([pair(["1"], ["1"], "model", 0.9)], [], [], [left])


def test_pairs_by_the_model_come_in_the_order_of_their_po_line():
    po_doc = po([line("", "1", desc=HOSE), line("", "1", desc="Ball valve DN15 brass")])
    doc = confirmation([line("", "1", desc=VALVE), line("", "1", desc=SHORT_HOSE)])                         # 5 / 6 and 0.75: candidates
    r = match_lines(doc, po_doc, ScriptedDecider({VALVE: ("2", 0.9), SHORT_HOSE: ("1", 0.95)}))
    assert r.pairs == [pair(["1"], ["2"], "model", 0.95), pair(["2"], ["1"], "model", 0.9)]


# --- (4) what is left, the order of the result, and random documents ---

def test_a_line_with_no_code_and_no_description_has_nothing_to_match_on():
    decider = FakeDecider("1", 0.99)
    r = match_lines(confirmation([line("", "5")]), po([line("", "5")]), decider)
    assert (r.pairs, r.unmatched_po, r.extra_doc, r.uncertain, decider.decisions) == ([], ["1"], ["1"], [], [])


def test_pairs_by_code_come_first_then_by_description_then_by_model_and_what_is_left_in_document_order():
    po_doc = po([line("", "1", desc=HOSE), line("KM-2", "2"), line("", "3", desc="Ball valve DN15 brass"), line("KM-4", "4"), line("KM-5", "5")])
    doc = confirmation([line("", "1", desc="Ball valve DN15, brass"), line("", "1", desc=SHORT_HOSE), line("KM-9", "9"), line("KM-2", "2"),
                        line("KM-8", "8")])
    r = match_lines(doc, po_doc, FakeDecider("1", 0.9))
    assert r.pairs == [pair(["2"], ["4"], "code", 1.0), pair(["3"], ["1"], "description", 42 / 43), pair(["1"], ["2"], "model", 0.9)]
    assert (r.unmatched_po, r.extra_doc, r.uncertain) == (["4", "5"], ["3", "5"], [])


CODES = ["KM-1", "KM-2", "KM-3", "KM-1 / KM-2", "KM-1-B", "6205", "6205-2RS", "10", "A-1", "A-1 / KM-2", "S-1", ""]
DESCRIPTIONS = ["", VALVE, "Ball valve DN25", HOSE, SHORT_HOSE, "Hydraulic hose", "Hose 10 mm", "Seal kit for KM-1 and KM-2", "uw artikelnummer KM-3"]


def random_lines(rng: random.Random) -> list[DocLine]:
    return [line(rng.choice(CODES), rng.choice(["", "3", "5"]), desc=rng.choice(DESCRIPTIONS)) for _ in range(rng.randint(0, 5))]


class RandomDecider:
    """Answers at random: one of the options, no choice or a choice outside them, with a confidence about the threshold."""
    name = "random"

    def __init__(self, rng: random.Random):
        self.rng = rng

    def decide(self, d: Decision) -> DecisionAnswer:
        return DecisionAnswer(choice=self.rng.choice([*d.options, None, "x"]), confidence=self.rng.choice([0.0, 0.5, 0.79, 0.8, 0.95]),
                              rationale="random", source=self.name)


class ContentDecider:
    """Answers from what a decision holds and not from the order of the lines: the document line and its options as a set."""
    name = "content"

    def decide(self, d: Decision) -> DecisionAnswer:
        options = sorted(d.options)
        n = sum(map(ord, f"{d.context['document_line']} {options}"))
        return DecisionAnswer(choice=[*options, None][n % (len(options) + 1)], confidence=[0.5, 0.79, 0.8, 0.95][n % 4], rationale="content",
                              source=self.name)


def canonical(r: MatchResult) -> tuple:
    """A result with every list sorted, to compare results whose lines came in another order."""
    def key(p: Pair) -> tuple:
        return sorted(p.po_line_ids), sorted(p.doc_line_ids), p.basis, p.confidence, p.kind
    return sorted(map(key, r.pairs)), sorted(map(key, r.uncertain)), sorted(r.unmatched_po), sorted(r.extra_doc)


def test_each_line_ends_in_one_place_whatever_the_decider_answers_and_in_whatever_order_the_lines_come():
    rng = random.Random(30)
    shapes = {"matched": lambda p: len(p.po_line_ids) == len(p.doc_line_ids) == 1, "split": lambda p: len(p.po_line_ids) == 1 < len(p.doc_line_ids),
              "merged": lambda p: len(p.doc_line_ids) == 1 < len(p.po_line_ids)}
    for _ in range(500):
        po_doc, doc = po(random_lines(rng)), confirmation(random_lines(rng))
        r = match_lines(doc, po_doc, RandomDecider(rng))
        paired = [i for p in r.pairs for i in p.po_line_ids]
        doubtful = {i for p in r.uncertain for i in p.po_line_ids}
        assert len(paired) == len(set(paired)) and not set(paired) & doubtful and not (set(paired) | doubtful) & set(r.unmatched_po)
        assert set(paired) | doubtful | set(r.unmatched_po) == {l.line_id for l in po_doc.lines}
        assert sorted([j for p in r.pairs + r.uncertain for j in p.doc_line_ids] + r.extra_doc) == sorted(l.line_id for l in doc.lines)
        assert all(shapes[p.kind](p) for p in r.pairs)
        shuffled_doc = doc.model_copy(update={"lines": rng.sample(doc.lines, len(doc.lines))})
        shuffled_po = po_doc.model_copy(update={"lines": rng.sample(po_doc.lines, len(po_doc.lines))})
        assert canonical(match_lines(shuffled_doc, shuffled_po, ContentDecider())) == canonical(match_lines(doc, po_doc, ContentDecider()))


# --- the whole made-up set ---

@pytest.mark.parametrize("case_id", CASES)
def test_the_made_up_set_gives_the_line_presence_of_its_references(case_id, madeup_out, tmp_path):
    """The perfect reading of each case against its PO, read from the PO PDF of the version the case answers (a cart's PO
    from its export), gives the line presence references.csv states: each PO line matched or merged with the document line
    the reference cites, or missing, and each extra document line."""
    assert len(CASES) == 22
    rows = [row for row in PRESENCE if row["case_id"] == case_id]
    if case_id.startswith("CART"):
        doc = document_from_spec(case_id, "cart")
        [version] = PoStore(tmp_path).from_export(madeup_out / f"{case_id}_export.csv", EXPORT_MAP)
        po_doc = version.document
        cited = {line.line_id: f"{case_id}.html, row {n}" for n, line in enumerate(doc.lines, 1)}
    else:
        doc = document_from_spec(case_id)
        po_doc = read_po_pdf(madeup_out / f"{case_id}_po_v{rows[0]['version_answered']}.pdf")
        cited = {line.line_id: f"{case_id}_conf.pdf, page {line.item_code.source.page}, line {line.item_code.source.line}" for line in doc.lines}
    r = match_lines(doc, po_doc, NoDecider())
    found = [(po_id, p.kind, cited[doc_id]) for p in r.pairs for po_id in p.po_line_ids for doc_id in p.doc_line_ids]
    found += [(po_id, "missing", "po") for po_id in r.unmatched_po] + [(doc_id, "extra", cited[doc_id]) for doc_id in r.extra_doc]
    assert sorted(found) == sorted((row["line_id"], row["line_label"], row["source"]) for row in rows)
    assert r.uncertain == [] and all((p.basis, p.confidence) == ("code", 1.0) for p in r.pairs)


# --- end to end, through the pipeline (final review, findings 10 and 11) ---

def run_mu08(tmp_path, madeup_out, change):
    """MU08 (line 1 confirmed at 12.70 against 12.50, within the price threshold) read by the parser, with `change` applied to its
    first line, run through the pipeline without a decider; returns the result."""
    from aid.pipeline import read_with_parser, run_case
    from aid.sources.upload import open_upload_case
    from aid.tests.conftest import deps

    def reader(path, kind):
        doc = read_with_parser(path, kind)
        return doc.model_copy(update={"lines": [change(doc.lines[0]), *doc.lines[1:]]})

    d = deps(tmp_path, madeup_out).model_copy(update={"reader": reader})
    c = open_upload_case(d.settings.cases_root, [madeup_out / "MU08_conf.pdf"], "250108", "confirmation", d.clock())
    return run_case(c, d)


def test_mu08_as_read_writes_its_price(tmp_path, madeup_out):
    r = run_mu08(tmp_path, madeup_out, lambda first: first)
    assert "po_line_price" in {e.op.kind for e in r.write_entries} and r.status == "closed"


def test_an_uncertain_item_code_writes_no_price_and_the_case_goes_to_review(tmp_path, madeup_out):
    r = run_mu08(tmp_path, madeup_out, lambda first: first.model_copy(update={"item_code": first.item_code.model_copy(update={"uncertain": True})}))
    assert not {"po_line_price", "article_price"} & {e.op.kind for e in r.write_entries}
    assert r.status == "review" and "match uncertain" in {item.reason for item in r.review_items}


def test_a_line_that_states_the_code_of_another_po_line_writes_no_price_and_the_case_goes_to_review(tmp_path, madeup_out):
    """MU08's line 1 keeps its description, Manometer 0-10 bar, but its item code states KM-0802, the code of PO line 2."""
    r = run_mu08(tmp_path, madeup_out, lambda first: first.model_copy(update={"item_code": DocValue(value="KM-0802/B", text="KM-0802/B")}))
    assert not {"po_line_price", "article_price"} & {e.op.kind for e in r.write_entries}
    assert r.status == "review" and "match uncertain" in {item.reason for item in r.review_items}
