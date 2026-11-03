"""Line matching: which lines of a supplier's document answer which lines of the PO.

match_lines pairs the lines of a document (a confirmation or a cart page) with the lines of the PO version it answers. A
Pair holds PO lines and the document lines that answer them, the rule that paired them (its basis), how sure that rule is
(its confidence) and the kind of pair in the words of the scoring manual (section 2): matched (one document line answers
one PO line), split (several document lines answer one PO line) or merged (one document line answers several PO lines).
The rules are tried in this order, each on the lines the rules before it left. Each document line ends in exactly one
place: one pair, one uncertain pair or extra_doc. Each PO line ends in one pair, in unmatched_po, or in the uncertain
pairs of the document lines whose candidate it is, which can be several; a PO line in a pair is in no uncertain pair.

A line's code is norm_code of its item code (the value, else the printed text). A text holds a PO code when the printed
code of a PO line stands in it as a whole word, in any case, outside every longer PO code it holds: the longest are found
first and no two overlap ("KM-1901 / KM-1902" holds KM-1901 and KM-1902, "KM-10" does not hold KM-1, and "6205-2RS"
holds 6205-2RS but not 6205 when both are PO codes). An item code is searched for every PO code, a description only for
the PO codes of MIN_HELD_LENGTH or more letters and digits. A document line whose own code is no PO code holds the codes
its item code holds, or when that holds none, the codes its description holds.

1. Codes. A document line whose own code is the code of a PO line answers it, whatever its texts hold: matched, or one
   split pair of all the document lines with that code. When the code is on several PO lines, a PO line and a document
   line are paired when their quantity (as a number, so 5 and 5.00 are one quantity) is on no other of those lines; each
   document line left has the PO lines of the code left as its candidates (rule 5), or goes on when none is left. A
   document line whose item code holds two or more PO codes is a merged line, which answers every PO line with those
   codes in one merged pair. A merged line is not settled by codes when one of its codes is on several PO lines, or when
   another document line claims one of its PO lines as well: it and the lines these claims join are one uncertain pair
   of kind merged. The pairs and uncertain pairs of this rule have confidence 1.0. A document line whose own code is a PO
   code but marked uncertain (the reader could not read it, or could not find it where its source says) makes no pair by
   code: once the pairs of this rule are made, the PO lines of its code that are left are its candidates (rule 5), and
   when none is left it goes on to the next rules. A merged line keeps its merged pair, which is never compared. Only the
   uncertain mark is read: a value without a source on a PDF with a text layer is one the LLM reader already marks
   uncertain (aid/readers/supplier_llm.py), while the Document does not say whether its PDF has a text layer.
2. Descriptions. The similarity of a document line's description a and a PO line's description b (each the value, else
   the printed text) is difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio(). The pairs of similarity SIM_MATCH or
   more are taken the most similar first: matched, with the similarity as confidence. Lines tie when one of them is as
   similar to two or more free lines as it is to any: the tied lines are then paired by quantity as in rule 1, and each
   tied document line left has as candidates the PO lines left that it is as similar to, or stays for the pairs of lower
   similarity when none is left. A document line paired so stays paired only when each PO code it holds is the code of
   the PO line it was paired with or is held by that PO line's description as well: the supplier echoed that
   description, codes and all. A code shorter than MIN_HELD_LENGTH is never found in a description, so it is never
   echoed. When a code it holds is neither, and that code is on other PO lines that no pair took, the line is not paired
   after all: it is one uncertain pair with the PO line it was paired with and the PO lines of all such codes, basis
   "description" with the similarity as confidence, and the decider is not asked. A document line paired so whose item
   code states the code of another PO line is not paired either, even when no pair took that line (see Another code
   below): it is one uncertain pair with its PO line, basis "description" with the similarity as confidence.
3. Held codes. A code held in a text is weaker evidence than a line's own code and never makes a pair by itself. On the
   PO lines left, one held code makes the PO lines with it the line's candidates (rule 5), and two or more held in the
   description make the PO lines with them one uncertain pair of kind merged (rule 5). A line whose held codes have no PO
   line left goes on.
4. Similar descriptions. Each document line left has as candidates the PO lines left whose similarity to it is
   SIM_CANDIDATE or more; a PO line can be a candidate of several document lines.
5. The decider. The decider is asked once for each document line with candidates, in document order, except for a line
   whose description holds several PO codes, with a Decision of kind "line_match": the options are the line ids of its
   candidates, and the context is the document line's code, description and quantity and each candidate's line id,
   code, description, quantity and similarity (None when a description is missing); for a line that holds a PO code,
   also that code as the PO prints it ("held_code") and the field it was found in ("found_in": "item_code" or
   "description"). The answers are then settled together. A choice among the options with a confidence of at least
   MIN_CONFIDENCE counts: the document lines that chose one PO line are one pair of basis "model", matched with the
   decider's confidence when one line chose it, split with the lowest of their confidences when several did. A PO line
   chosen so is no candidate of the other lines any more. When the item code of one of the document lines that chose a
   PO line states the code of another PO line (see Another code below), none of them is paired: each is one uncertain
   pair with that PO line, basis "model" with its confidence. Each other document line with candidates left is one
   uncertain pair with them: basis "model" with the decider's confidence when it chose one of them, else the basis of
   its candidates, with their best similarity for candidates by description, 1.0 for candidates by the line's own code
   and HELD_CONFIDENCE for candidates by a code its text holds or by its own code marked uncertain; the uncertain pair
   of a description that holds several PO codes is of kind merged. A line whose candidates were all chosen for other
   lines is extra.
6. What is left. The PO lines in no pair and no uncertain pair are unmatched_po and such document lines are extra_doc,
   each in the order of its document. A line with neither a code nor a description has nothing to match on and always
   ends here.

Another code. A document line states the code of another PO line than the one it is paired with when its item code
holds (as rule 1 finds held codes, a whole word) the code of a PO line that is not that line, and does not hold that
line's own code ("KM-1901 alt" holds KM-1901, so it states KM-1901). A pair by description or by the model on such lines
points one of the PO's codes elsewhere: it may be two different articles that are described alike, so it is never a pair,
only an uncertain one. A code that is no PO line's code (the supplier's own numbering, "S-7") is no claim about the PO's
codes and no conflict, and a document line without a code is paired as the rules say.

Never force: a line that the rules and the decider cannot settle goes to uncertain, never into a pair, and a line in an
uncertain pair is neither unmatched nor extra; such a line is not compared, and its case goes to review. The pairs are
listed in the order they were made: the pairs by code in the order of their first PO line, then the pairs by
description, the most similar first, then the pairs by the model in the order of their PO line. Which lines are paired
does not depend on the order of the lines, only on the decider's answers. Of the values marked uncertain only a
document line's own item code changes the matching (rule 1): any other uncertain value is left to the comparison and the
routing, which never compare it.
"""
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Literal, NamedTuple

from pydantic import BaseModel

from aid.decider import MIN_CONFIDENCE, Decider, Decision
from aid.schema import DocLine, DocValue, Document

SIM_MATCH, SIM_CANDIDATE = 0.9, 0.6
HELD_CONFIDENCE = 0.5                            # of an uncertain pair that rests on a PO code a line's text holds
MIN_HELD_LENGTH = 3                              # a PO code of fewer letters and digits is never looked for in a text

Basis = Literal["code", "description", "model"]
Kind = Literal["matched", "split", "merged"]
Link = tuple[int, int]                           # a PO line and a document line, by their index


class _Candidates(NamedTuple):
    """The PO lines a document line waits on for the decider (rule 3), and what made them its candidates."""
    basis: Basis                                 # "code" or "description"
    po: list[int]                                # in PO order
    held: tuple[str, str] | None = None          # the PO code the line's text holds, as the PO prints it, and its field
    merged: bool = False                         # a description that holds several PO codes: no question, an uncertain merged pair
    unsure: bool = False                         # the line's own code, marked uncertain


class Pair(BaseModel):
    po_line_ids: list[str]
    doc_line_ids: list[str]
    basis: Basis
    confidence: float                            # from 0 to 1
    kind: Kind


class MatchResult(BaseModel):
    pairs: list[Pair]
    unmatched_po: list[str]                      # the PO lines no document line answers, in the order of the PO
    extra_doc: list[str]                         # the document lines that answer no PO line, in the order of the document
    uncertain: list[Pair]                        # what the rules and the decider could not settle; its lines are in neither list above


def norm_code(text: str | None) -> str | None:
    """An item code without case and punctuation: its letters and digits only, upper-case ("km-1901" gives "KM1901").
    None, or a text without a letter or a digit, gives None."""
    return "".join(char for char in (text or "").upper() if char.isalnum()) or None


def match_lines(doc: Document, po: Document, decider: Decider) -> MatchResult:
    """The pairs of the lines of `doc` with the lines of `po`, by the rules of the module docstring. Only the decider is
    asked anything, once for each document line with candidates."""
    matching = _Matching(doc.lines, po.lines)
    matching.by_code()
    matching.by_description()
    matching.by_held_code()
    matching.by_similarity()
    matching.by_model(decider)
    return matching.result()


class _Matching:
    """One run of the rules on the lines of a document and of a PO, which it refers to by their index."""

    def __init__(self, doc: list[DocLine], po: list[DocLine]):
        self.doc, self.po = doc, po
        self.pairs: list[Pair] = []
        self.uncertain: list[Pair] = []
        self.free_doc, self.free_po = set(range(len(doc))), set(range(len(po)))     # the lines the next rule may use
        self.used_doc: set[int] = set()                                             # the lines in a pair or an uncertain pair
        self.used_po: set[int] = set()
        self.candidates: dict[int, _Candidates] = {}                                # document line -> its candidates
        self.groups: dict[str, list[int]] = {}                                      # a PO code -> the PO lines with it, in PO order
        self.held: dict[int, tuple[set[str], str]] = {}                             # document line -> the PO codes it holds, and their field
        self.words: list[tuple[str, re.Pattern[str]]] = []                          # each PO code and the pattern of its printed form
        self.description_words: list[tuple[str, re.Pattern[str]]] = []              # those that a description is searched for

    def record(self, into: list[Pair], po: list[int], doc: list[int], basis: Basis, confidence: float, kind: Kind = "matched") -> Pair:
        """Record the PO lines `po` and the document lines `doc` as one pair in `into` (the pairs or the uncertain pairs)."""
        pair = Pair(po_line_ids=[self.po[i].line_id for i in po], doc_line_ids=[self.doc[j].line_id for j in doc], basis=basis,
                    confidence=confidence, kind=kind)
        into.append(pair)
        self.used_po.update(po)
        self.used_doc.update(doc)
        self.free_po.difference_update(po)
        self.free_doc.difference_update(doc)
        return pair

    def propose(self, doc: int, po: list[int], basis: Basis, held: tuple[str, str] | None = None, merged: bool = False,
                unsure: bool = False) -> None:
        """Leave the choice of document line `doc` among the PO lines `po` to the decider; the rules leave these lines alone."""
        self.candidates[doc] = _Candidates(basis, po, held, merged, unsure)
        self.free_doc.discard(doc)
        self.free_po.difference_update(po)

    def by_code(self) -> None:
        """Rule 1 of the module docstring; it also finds the codes the other lines hold, for rules 2 and 3."""
        for i, line in enumerate(self.po):
            if code := norm_code(_text(line.item_code)):
                self.groups.setdefault(code, []).append(i)
        groups = self.groups
        single, merged, self.held, unsure = self.claims()
        found: list[tuple[list[int], list[int], Kind]] = []                         # the pairs by code: PO lines, document lines, kind
        for codes in _settled_together(groups, merged):
            po = sorted(i for code in codes for i in groups[code])
            joined = [j for j, held in merged.items() if held <= codes]
            alone = [j for j, code in single.items() if code in codes]
            if joined and (len(joined) + len(alone) > 1 or len(po) > len(codes)):  # a PO line claimed twice, or a code on several PO lines
                self.record(self.uncertain, po, sorted(joined + alone), "code", 1.0, "merged")
            elif joined:
                found.append((po, joined, "merged"))
            elif alone and len(po) == 1:
                found.append((po, alone, "split" if len(alone) > 1 else "matched"))
            elif alone:
                found += [([i], [j], "matched") for i, j in self.by_quantity(po, alone, {(i, j) for i in po for j in alone}, "code")]
        for po, doc, kind in sorted(found, key=lambda pair: pair[0][0]):
            self.record(self.pairs, po, doc, "code", 1.0, kind)
        free = set(self.free_po)                                                    # what the pairs by code left, the same for every line
        for j, code in unsure.items():
            if po := [i for i in groups[code] if i in free]:
                self.propose(j, po, "code", unsure=True)

    def claims(self) -> tuple[dict[int, str], dict[int, set[str]], dict[int, tuple[set[str], str]], dict[int, str]]:
        """What each document line claims by code: the lines whose own code is a PO code, with that code; the merged lines,
        with the PO codes their item code holds; the other lines whose texts hold PO codes, with those codes and the
        field that holds them; and the lines whose own code is a PO code but marked uncertain, with that code."""
        self.words = [(code, _whole_word(_printed(self.po[i].item_code))) for code, lines in self.groups.items() for i in lines]
        self.description_words = [(code, word) for code, word in self.words if len(code) >= MIN_HELD_LENGTH]
        single: dict[int, str] = {}
        merged: dict[int, set[str]] = {}
        held: dict[int, tuple[set[str], str]] = {}
        unsure: dict[int, str] = {}
        for j, line in enumerate(self.doc):
            if (own := norm_code(_text(line.item_code))) in self.groups:
                (unsure if line.item_code.uncertain else single)[j] = own
            elif len(codes := _held(_printed(line.item_code), self.words)) > 1:
                merged[j] = codes
            elif codes:
                held[j] = (codes, "item_code")
            elif codes := _held(_printed(line.description), self.description_words):
                held[j] = (codes, "description")
        return single, merged, held, unsure

    def by_quantity(self, po: list[int], doc: list[int], links: set[Link], basis: Basis) -> list[Link]:
        """The links between the PO lines `po` and the document lines `doc` (rule 1's lines of one code, rule 2's tied lines)
        that the quantity settles: a quantity on no other of these lines. Each document line left has as candidates the PO
        lines left that it is linked with; one with none left stays for the next rule."""
        po_quantity = {i: _quantity(self.po[i]) for i in po}
        doc_quantity = {j: _quantity(self.doc[j]) for j in doc}
        po_count, doc_count = Counter(po_quantity.values()), Counter(doc_quantity.values())
        paired = [(i, j) for i, j in sorted(links) if po_quantity[i] is not None and po_quantity[i] == doc_quantity[j]
                  and po_count[po_quantity[i]] == doc_count[doc_quantity[j]] == 1]
        paired_po, paired_doc = {i for i, _ in paired}, {j for _, j in paired}
        for j in doc:
            left = [i for i in po if i not in paired_po and (i, j) in links]
            if left and j not in paired_doc:
                self.propose(j, left, basis)
        return paired

    def by_description(self) -> None:
        """Rule 2 of the module docstring."""
        ratios = self.ratios()
        made: list[tuple[Pair, int, int, float]] = []                               # the pairs of this rule: the pair, PO line, document line, ratio
        for ratio in sorted({ratio for ratio in ratios.values() if ratio >= SIM_MATCH}, reverse=True):         # the most similar first
            links = {link for link, r in ratios.items() if r == ratio and link[0] in self.free_po and link[1] in self.free_doc}
            for po, doc, tied in _connected(links):                                 # with more than one link, the lines tie
                settled = [(po[0], doc[0])] if len(tied) == 1 else self.by_quantity(po, doc, tied, "description")
                made += [(self.record(self.pairs, [i], [j], "description", ratio), i, j, ratio) for i, j in settled]
        free = set(self.free_po)                                                    # what the pairs left, the same for every line
        for pair, i, j, ratio in made:
            codes, _ = self.held.get(j, (set(), ""))
            echoed = _held(_printed(self.po[i].description), self.description_words) if codes else set()
            absent = [code for code in sorted(codes) if code not in echoed and i not in self.groups[code]]   # neither its code nor echoed
            if others := sorted(h for code in absent for h in self.groups[code] if h in free):
                self.pairs.remove(pair)                                             # a code it holds points to other PO lines
                self.record(self.uncertain, sorted([i, *others]), [j], "description", ratio)
            elif self.another_code(i, j):
                self.pairs.remove(pair)                                             # it states the code of another article
                self.record(self.uncertain, [i], [j], "description", ratio)

    def by_held_code(self) -> None:
        """Rule 3 of the module docstring."""
        free = set(self.free_po)                                                    # what the rules before left, the same for every line
        for j, (codes, field) in self.held.items():
            if j in self.free_doc and (po := sorted(i for code in codes for i in self.groups[code] if i in free)):
                if len(codes) > 1:
                    self.propose(j, po, "code", merged=True)
                else:
                    self.propose(j, po, "code", held=(_printed(self.po[po[0]].item_code), field))

    def by_similarity(self) -> None:
        """Rule 4 of the module docstring."""
        ratios = self.ratios()
        proposals = {j: [i for i in sorted(self.free_po) if ratios.get((i, j), 0.0) >= SIM_CANDIDATE] for j in sorted(self.free_doc)}
        for j, po in proposals.items():
            if po:
                self.propose(j, po, "description")

    def ratios(self) -> dict[Link, float]:
        """The similarity of each free PO line and free document line that both have a description."""
        return {(i, j): ratio for i in sorted(self.free_po) for j in sorted(self.free_doc)
                if (ratio := _similarity(self.doc[j], self.po[i])) is not None}

    def by_model(self, decider: Decider) -> None:
        """Rule 5 of the module docstring."""
        answers = {j: decider.decide(self.decision(j, waiting)) for j, waiting in sorted(self.candidates.items()) if not waiting.merged}
        chosen: dict[int, list[int]] = {}                                           # a PO line -> the document lines that chose it
        for j, answer in answers.items():
            po = self.candidates[j].po
            ids = [self.po[i].line_id for i in po]
            if answer.choice in ids and answer.confidence >= MIN_CONFIDENCE:
                chosen.setdefault(po[ids.index(answer.choice)], []).append(j)
        for i, doc in sorted(chosen.items()):
            if any(self.another_code(i, j) for j in doc):                         # one of them states the code of another article
                for j in doc:
                    self.record(self.uncertain, [i], [j], "model", answers[j].confidence)
            else:
                self.record(self.pairs, [i], doc, "model", min(answers[j].confidence for j in doc), "split" if len(doc) > 1 else "matched")
        for j, waiting in sorted(self.candidates.items()):
            left = [i for i in waiting.po if i not in chosen]
            if j in self.used_doc or not left:                                      # in a pair, or extra: its candidates went to other lines
                continue
            answer = answers.get(j)
            if answer is not None and answer.choice in [self.po[i].line_id for i in left]:
                self.record(self.uncertain, left, [j], "model", answer.confidence)
            elif waiting.basis == "description":
                self.record(self.uncertain, left, [j], "description", max(_similarity(self.doc[j], self.po[i]) for i in left))
            else:
                self.record(self.uncertain, left, [j], "code", HELD_CONFIDENCE if waiting.held or waiting.merged or waiting.unsure else 1.0,
                            "merged" if waiting.merged else "matched")

    def another_code(self, i: int, j: int) -> bool:
        """Whether document line j states the code of another PO line than PO line i (Another code, in the module docstring)."""
        stated = _held(_printed(self.doc[j].item_code), self.words)
        return norm_code(_text(self.po[i].item_code)) not in stated and any(i not in self.groups[code] for code in stated)

    def decision(self, j: int, waiting: _Candidates) -> Decision:
        """The question to the decider: which of its candidates document line j answers (rule 3)."""
        candidates = [{"line_id": self.po[i].line_id, **_summary(self.po[i]), "ratio": _similarity(self.doc[j], self.po[i])} for i in waiting.po]
        context = {"document_line": _summary(self.doc[j]), "candidates": candidates}
        if waiting.held:
            context["held_code"], context["found_in"] = waiting.held
        return Decision(kind="line_match", options=[self.po[i].line_id for i in waiting.po], context=context)

    def result(self) -> MatchResult:
        """Rule 6 of the module docstring: the pairs made, and the lines in none of them."""
        return MatchResult(pairs=self.pairs, unmatched_po=[line.line_id for i, line in enumerate(self.po) if i not in self.used_po],
                           extra_doc=[line.line_id for j, line in enumerate(self.doc) if j not in self.used_doc], uncertain=self.uncertain)


def _settled_together(groups: dict[str, list[int]], merged: dict[int, set[str]]) -> list[frozenset[str]]:
    """The PO codes in the sets that rule 1 settles together: the codes a merged line holds are in one set, which takes in
    the set of each of them. The sets come in the order of their first PO line."""
    together = {code: {code} for code in groups}
    for codes in merged.values():
        joined = set().union(*(together[code] for code in codes))
        for code in joined:
            together[code] = joined
    return sorted({frozenset(codes) for codes in together.values()}, key=lambda codes: min(groups[code][0] for code in codes))


def _connected(links: set[Link]) -> list[tuple[list[int], list[int], set[Link]]]:
    """The parts of a set of links between PO lines and document lines that hang together: the PO lines, document lines and
    links of each part, in the order of their first PO line."""
    parts, left = [], sorted(links)
    while left:
        po, doc = {left[0][0]}, {left[0][1]}
        while grown := [link for link in left if (link[0] in po) != (link[1] in doc)]:
            po.update(i for i, _ in grown)
            doc.update(j for _, j in grown)
        part = {link for link in left if link[0] in po}
        parts.append((sorted(po), sorted(doc), part))
        left = [link for link in left if link not in part]
    return parts


def _held(text: str | None, words: list[tuple[str, re.Pattern[str]]]) -> set[str]:
    """The PO codes a text holds (rule 1): the printed codes that stand in it as whole words, the longest found first and
    none overlapping one found before it."""
    if not text:
        return set()
    found = sorted(((match.start(), match.end(), code) for code, word in words for match in word.finditer(text)),
                   key=lambda span: (span[0] - span[1], span[0]))                   # the longest first, then from the left
    held: set[str] = set()
    taken: list[tuple[int, int]] = []
    for start, end, code in found:
        if all(end <= first or last <= start for first, last in taken):
            taken.append((start, end))
            held.add(code)
    return held


def _first(*texts: str | None) -> str | None:
    """The first of the texts that holds more than white space, or None."""
    return next((text for text in texts if text and text.strip()), None)


def _text(value: DocValue) -> str | None:
    """A value as the rules read it: the normalised value, else the printed text."""
    return _first(value.value, value.text)


def _printed(value: DocValue) -> str | None:
    """A value as the document prints it: the printed text, else the normalised value."""
    return _first(value.text, value.value)


def _whole_word(text: str) -> re.Pattern[str]:
    """The pattern that finds `text` as a whole word in any case: not as part of a longer run of letters, digits and "_"."""
    return re.compile(rf"(?<!\w){re.escape(text.strip())}(?!\w)", re.IGNORECASE)


def _quantity(line: DocLine) -> Decimal | None:
    """The quantity of a line as a number; None when the line has none."""
    try:
        number = Decimal(line.quantity.value)
    except (TypeError, InvalidOperation):                                           # no value, or not a number
        return None
    return number if number.is_finite() else None


def _similarity(doc_line: DocLine, po_line: DocLine) -> float | None:
    """The similarity of the descriptions of a document line and a PO line (rule 2); None when one of them has none."""
    a, b = _text(doc_line.description), _text(po_line.description)
    return None if a is None or b is None else SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _summary(line: DocLine) -> dict:
    """The code, description and quantity of a line, as the decider sees them."""
    return {"item_code": _text(line.item_code), "description": _text(line.description), "quantity": _text(line.quantity)}
