"""A seeded property test of the list of changes to enter in Exact (Task 35, third review round, K27; made to check what it claims
in the fourth, K31 and K32), built on the idea of the checkers' model-based harness: random sequences of the events of one place, the
price of PO 250108's line 1, driven through the real code (by_hand, hand_list.take_back, the page's "Weer op de lijst zetten" as
page.py makes it, undo_case through the prepared writer, capture_server._close_history), beside a small model of what Exact holds.

The model. Exact changes only when the buyer enters a value, and he enters it when he ticks a change or a put-back as entered; taking
such a tick back says the tick was a mistake, and Exact goes back to the value it held before the tick that covered those entries. A
drop changes nothing in Exact.

The invariants, after every step:
- never two open changes at the place;
- no open change asks the value Exact holds;
- no open change has a put-back (K11);
- a put-back the page put on the list stays: it is open, entered, dropped or seen, or a newer row of the place replaced it (K10, K20);
- the pre-check judges each prepared write by K19's two clauses: the put-back linked to an entry of the change that holds its row,
  else, for a row that a later row replaced while Exact held its value, the put-back that took that value out of Exact (the first
  change entered after the row that sets another value, when it puts back a change of the row's value: the put-back that replaced
  the row, one of that same change made again, or that of the change whose value the row asked again; K29). Such a put-back entered
  or seen counts the write as undone, open as waiting; while prepared_changes.csv or marks.jsonl cannot be read, no prepared write
  counts as made and none as waiting (K30);
- taking a tick back never cancels an undo of one of the change's cases that was entered or seen (K26), and a change with a put-back
  never returns to the list (K11, by the invariant above);
and, for each press of "Terugdraaien in Exact", that its note, read by its whole text, is true:
- the note that sends the buyer to the researcher never comes (K20);
- a note that writes nothing wrote nothing, but for the one line of marks.jsonl that links an undo made with the recipe, which
  take_back writes before it says that the put-back is on the list or does not return to it;
- "Nog niet teruggedraaid ..." names the put-back on the list, and it is open;
- "Deze wijziging is al teruggedraaid." is said only while Exact holds the change's old value, or once the buyer entered its put-back
  (or while an entered undo of the recipe that the list passed over and counts again sets the marks apart from Exact: a limit of the
  recipe, MANUAL 3.10);
- a refusal names the change that hand_list._newer gives, in the state it says, with the note its rule gives (K21's when it asks
  the old value, K23's when it can never be put back), and its advice can be followed: after the buyer drops the newer change,
  enters it, or puts it back and ticks its put-back off, the same refusal never comes again (K13, K21, K22), but while an open undo
  made with the recipe stands after that put-back (K22's "else the note as now");
- no refusal, and no "komt niet terug", while Exact holds the change's old value already (K20 b); and "komt niet terug" only while
  something stands in the way of a new put-back: a change on the list, or a newer change whose value Exact holds (K22).

The sequences: thirteen shortest sequences, A to D from the checkers' minimal/ folder, E to H found in the third review round and I
to M in the fourth (I after the checkers' K19_newest_put_back_clean, K after their probe_model_test_never), and a fixed set of seeds
of the random generator below. The seeds are this generator's own: none replays a sequence of the checkers' harness, whose
generator is another. The whole runs in a few seconds, so that the invariants stay checked whenever the list's rules change.
"""
import json
import pathlib
import random
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aid.capture_server import _close_history
from aid.case import load_case, open_case
from aid.decider import NoDecider
from aid.execution import by_hand
from aid.execution.by_hand import FOLDER, MARKS_FILE, _marks, changes, mark, reopen
from aid.execution.ops import WriteOp
from aid.execution.writer import PREPARED_FILE, UNDO_RULE_ID, PreparedWriter, WriteLog, undo_case
from aid.pipeline import Deps, read_with_parser
from aid.po_store import PoStore
from aid.review import hand_list
from aid.review.i18n import STRINGS
from aid.tests.conftest import SETTINGS

NL = STRINGS["nl"]
PO, VALUES, START = "250108", ["12.50", "12.55", "12.60", "12.70", "12.90"], datetime(2026, 11, 12, 9, 0)
SILENT = ("hand_put_back_not_entered", "hand_put_back_unknown", "hand_put_back_cannot", "hand_put_back_newer_open",
          "hand_put_back_newer_entered", "hand_put_back_newer_never", "hand_put_back_not_needed", "hand_put_back_lost",
          "hand_put_back_on_list", "hand_reopen_not", "hand_reopen_seen", "hand_reopen_put_back")     # notes of a press that writes nothing
NAMING = ("hand_put_back_newer_open", "hand_put_back_newer_entered", "hand_put_back_newer_never", "hand_put_back_not_needed",
          "hand_put_back_listed")
REFUSALS = ("hand_put_back_newer_open", "hand_put_back_newer_entered", "hand_put_back_newer_never", "hand_put_back_not_needed")

# The checkers' shortest sequences (their minimal/ folder), in the events of this test.
MINIMAL = {
    "A": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.70", "new": "12.50"}, {"t": "put_back", "label": "r0"}, {"t": "tick", "what": "dropped", "label": "r2"},
          {"t": "put_back", "label": "r0"}],
    "B": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.50", "new": "12.60"}, {"t": "tick", "what": "entered", "label": "r2"}, {"t": "put_back", "label": "r0"},
          {"t": "put_back", "label": "r2"}, {"t": "tick", "what": "entered", "label": "p5"}, {"t": "put_back", "label": "r0"}],
    "C": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.50", "new": "12.60"}, {"t": "tick", "what": "entered", "label": "r2"}, {"t": "put_back", "label": "r2"},
          {"t": "ask", "old": "12.50", "new": "12.55"}, {"t": "tick", "what": "dropped", "label": "r5"}, {"t": "put_back", "label": "r0"},
          {"t": "put_back", "label": "r2"}, {"t": "put_back", "label": "r0"}],
    "D": [{"t": "ask", "old": "12.50", "new": "12.70", "case": "c0"}, {"t": "ask", "old": "12.50", "new": "12.70", "case": "c1"},
          {"t": "tick", "what": "entered", "label": "r0"}, {"t": "undo_case", "case": "c1"}, {"t": "tick", "what": "entered", "label": "u3_0"},
          {"t": "reopen", "label": "r0"}],
}
# Four found by the harness during the third review round: a newer change that no longer stands in Exact named by the refusal (E
# and F), one that answered "al teruggedraaid" for a value Exact no longer holds (G: the 12,90 was asked against a version that
# still showed 12,70, and its put-back set 12,70 again), and a replaced put-back that the advice asks for, with the rows after it
# put back themselves (H).
MINIMAL |= {
    "E": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.50", "new": "12.60"}, {"t": "tick", "what": "entered", "label": "r2"},
          {"t": "ask", "old": "12.50", "new": "12.55"}, {"t": "tick", "what": "entered", "label": "r4"}, {"t": "put_back", "label": "r4"},
          {"t": "tick", "what": "entered", "label": "p6"}, {"t": "put_back", "label": "r0", "refused": "hand_put_back_newer_entered",
                                                           "named": "r2"}],
    "F": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.70", "new": "12.90"}, {"t": "tick", "what": "entered", "label": "r2"},
          {"t": "ask", "old": "12.70", "new": "12.80"}, {"t": "tick", "what": "entered", "label": "r4"}, {"t": "put_back", "label": "r4"},
          {"t": "tick", "what": "entered", "label": "p6"}, {"t": "undo_case", "case": "c2"}, {"t": "put_back", "label": "r2"},
          {"t": "put_back", "label": "r0", "refused": "hand_put_back_newer_entered", "named": "r2"}],
    "G": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.70", "new": "12.50"}, {"t": "tick", "what": "entered", "label": "r2"},
          {"t": "ask", "old": "12.70", "new": "12.90"}, {"t": "tick", "what": "entered", "label": "r4"}, {"t": "put_back", "label": "r4"},
          {"t": "tick", "what": "entered", "label": "p6"}, {"t": "put_back", "label": "r0"}],
    "H": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.50", "new": "12.60"}, {"t": "tick", "what": "entered", "label": "r2"}, {"t": "put_back", "label": "r2"},
          {"t": "ask", "old": "12.60", "new": "12.50"}, {"t": "tick", "what": "entered", "label": "r5"}, {"t": "put_back", "label": "r5"},
          {"t": "tick", "what": "entered", "label": "p7"}, {"t": "put_back", "label": "r0"}, {"t": "put_back", "label": "r2"},
          {"t": "tick_put_back", "what": "entered", "label": "r2"},
          {"t": "put_back", "label": "r0", "refused": "hand_put_back_newer_entered", "named": "r2"}],
}
# Two of the fourth review round, for K19's second clause (K29): a row replaced by the put-back of the change whose value it asked
# again, while the put-back of another change of that value waits and is dropped (I, the checkers' K19_newest_put_back_clean); and a
# row replaced by a newer document, whose value the put-back of that change takes out of Exact all the same (J, the harness's seed
# 7330).
MINIMAL |= {
    "I": [{"t": "ask", "old": "12.50", "new": "12.90"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.90", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r2"},
          {"t": "ask", "old": "12.70", "new": "12.90"}, {"t": "tick", "what": "entered", "label": "r4"},
          {"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r6"},
          {"t": "ask", "old": "12.70", "new": "12.90"}, {"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "put_back", "label": "r6"},
          {"t": "tick", "what": "entered", "label": "p10"}, {"t": "put_back", "label": "r2"},
          {"t": "tick", "what": "dropped", "label": "p12"}],
    "J": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.70", "new": "12.90"}, {"t": "ask", "old": "12.50", "new": "12.70"},
          {"t": "ask", "old": "12.70", "new": "12.50"}, {"t": "tick", "what": "dropped", "label": "r4"}, {"t": "put_back", "label": "r0"},
          {"t": "tick", "what": "entered", "label": "p6"}],
}
# Three more for rules that the random seeds reach seldom: K23's note for a newer change that the aid can never put back (K, the
# checkers' probe_model_test_never), a replaced put-back whose old value a newer change entered (L), and a dropped put-back that a
# dropped newer row follows (M).
MINIMAL |= {
    "K": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.70", "new": "12.90", "case": "c2"}, {"t": "tick", "what": "entered", "label": "r2"},
          {"t": "undo_case", "case": "c2"}, {"t": "tick", "what": "entered", "label": "u4_0"},
          {"t": "ask", "old": "12.90", "new": "12.70"}, {"t": "put_back", "label": "r0"}],
    "L": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"},
          {"t": "ask", "old": "12.70", "new": "12.90"}, {"t": "tick", "what": "entered", "label": "r2"}, {"t": "put_back", "label": "r2"},
          {"t": "ask", "old": "12.90", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r5"}, {"t": "put_back", "label": "r2"}],
    "M": [{"t": "ask", "old": "12.50", "new": "12.70"}, {"t": "tick", "what": "entered", "label": "r0"}, {"t": "put_back", "label": "r0"},
          {"t": "tick", "what": "dropped", "label": "p2"}, {"t": "ask", "old": "12.50", "new": "12.60"},
          {"t": "tick", "what": "dropped", "label": "r4"}, {"t": "put_back", "label": "r0"}],
}
SEEDS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 62, 89, 109, 144, 182, 233, 377, 610, 987, 1597, 2584]   # this generator's own
STEPS = 30


def same(one: str | None, other: str | None) -> bool:
    return one is not None and other is not None and Decimal(one) == Decimal(other)


class Broken(AssertionError):
    """An invariant that does not hold, with the trace of the sequence."""


@contextmanager
def held(name: str):
    """While it lasts, the file of the list with this name cannot be read: a PermissionError, as Windows gives for a file another
    program holds."""
    real = pathlib.Path.read_bytes

    def read_bytes(self):
        if self.name == name:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(self))
        return real(self)

    pathlib.Path.read_bytes = read_bytes
    try:
        yield
    finally:
        pathlib.Path.read_bytes = real


class World:
    """One folder with its list, its cases and the model of what Exact holds; events refer to rows by labels."""

    def __init__(self, folder: Path):
        shutil.rmtree(folder, ignore_errors=True)
        self.cases_root = folder / "cases"
        self.prepared = self.cases_root / FOLDER
        self.now = START
        self.deps = Deps(settings=SETTINGS.model_copy(update={"cases_root": self.cases_root}), store=PoStore(folder / "store"),
                         reader=read_with_parser, decider=NoDecider(), writer=PreparedWriter(self.prepared), mailer=None,
                         clock=lambda: self.now)
        self.labels: dict[str, str] = {}                 # label -> entry id
        self.cases: dict[str, str] = {}                  # case label -> case id
        self.exact = "12.50"                             # what Exact holds
        self.ticks: list[tuple[frozenset, str]] = []     # each tick as entered: the entry ids it covered, the value Exact held before
        self.accepted: set[str] = set()                  # the put-backs the page put on the list
        self.trace: list[str] = []
        self.pending: list[dict] = []                    # the advice of the last refusal, to follow
        self.last: tuple[str, str | None] = ("", None)   # the key of the last press's note, and the change it named

    # --- helpers ---
    def found(self):
        found, passed = changes(self.prepared)
        assert passed == 0
        return found

    def change_of(self, label: str):
        entry = self.labels.get(label)
        return next((change for change in self.found() if entry in change.ids), None) if entry else None

    def label_of(self, change) -> str:
        reverse = {entry: label for label, entry in self.labels.items()}
        return next((reverse[entry] for entry in change.ids if entry in reverse), "?")

    def files(self) -> dict:
        return {path: path.read_bytes() for path in self.cases_root.rglob("*") if path.is_file()}

    def links(self) -> dict[str, str]:
        return _marks(self.prepared / MARKS_FILE)[1]

    def fail(self, what: str):
        raise Broken(f"{what}\n" + "\n".join(self.trace) + "\n" + "\n".join(
            f"   {c.old_value}->{c.new_value} {c.rule_id} {c.state} {[self.label_of(c)]} puts_back={c.puts_back and c.puts_back[:6]}"
            for c in self.found()) + f"\n   Exact holds {self.exact}")

    def key(self, note: str) -> str:
        """The key of a note, read by its whole text: K23's note begins as K13's for an entered change."""
        for key, text in NL.items():
            if key.startswith("hand_") and "{" not in text and text == note:
                return key
        note = note.partition(" Let op: ")[0]        # the sentence about another old value follows the note of a put-back made (W10)
        for key in NAMING:
            if re.fullmatch(re.escape(NL[key]).replace(re.escape("{row}"), ".+"), note, re.S):
                return key
        return "?" + note

    def other_before(self, change, put, found: list) -> str:
        """The sentence that the note of a put-back made now ends with when the aid's own records give another value for what Exact
        held before the change than the put-back names (the review round of Task 43, W10): by the marks, the value of the last
        change of the place entered or seen before the change put back; else what the order as it stands shows, when that is
        neither the value entered nor the value named. "" when the records give no other value."""
        place = by_hand.place_of(change)
        at = next(index for index, one in enumerate(found) if one.ids == change.ids)
        held = [one.new_value for one in found[:at] if by_hand.place_of(one) == place and one.state in ("entered", "seen")]
        known, source = (held[-1], "marks") if held else (by_hand.shown_now(put, self.deps.store, self.cases_root), "look")
        if known is None or same(known, put.new_value) or (source == "look" and same(known, put.old_value)):
            return ""
        return " " + NL["hand_put_back_other"].format(old=hand_list._value(put.new_value, put.kind, "nl"),
                                                      value=hand_list._value(known, put.kind, "nl"), source=NL[f"hand_put_back_source:{source}"])

    def entered(self) -> set[str]:
        """The entry ids that a tick as entered covered, and that no tick taken back since took away."""
        return {entry for ids, _ in self.ticks for entry in ids}

    def recipe_link_only(self, files: dict) -> bool:
        """Whether the files differ from `files` only by lines of marks.jsonl that link an undo made with the recipe (undo_case) to the
        entry it undoes: the link that take_back writes for such an undo before it says what became of it."""
        now = self.files()
        marks = self.prepared / MARKS_FILE
        if set(now) != set(files) | {marks} or any(now[path] != files[path] for path in files if path != marks):
            return False
        before, after = files.get(marks, b""), now.get(marks, b"")
        recipe = {entry for label, entry in self.labels.items() if label.startswith("u")}
        lines = [json.loads(line) for line in after[len(before):].splitlines() if line.strip()] if after.startswith(before) else None
        return bool(lines) and all(line.get("mark") == "puts_back" and line.get("entry_id") in recipe for line in lines)

    # --- events ---
    def ask(self, ev, i):
        case_label = ev.get("case") or f"c{i}"
        if case_label not in self.cases:
            self.cases[case_label] = open_case(self.cases_root, "confirmation", [], PO, self.now + timedelta(seconds=i)).case_id
        op = WriteOp(kind="po_line_price", po_number=PO, version=1, line_id="1", old_value=ev["old"], new_value=ev["new"])
        entry = PreparedWriter(self.prepared).apply(op, self.cases[case_label], "R04", {}, self.now)
        WriteLog(self.cases_root / self.cases[case_label]).append(entry)
        self.labels[f"r{i}"] = entry.entry_id
        return f"ask {ev['old']}->{ev['new']} {case_label} -> r{i}"

    def tick(self, ev, i):
        change = self.change_of(ev["label"])
        if change is None or change.state != "open":
            return None
        if mark(self.prepared, change.ids, ev["what"], self.now) == 0:
            self.fail("a tick of an open change wrote nothing")
        if ev["what"] == "entered":
            self.ticks.append((frozenset(change.ids), self.exact))
            self.exact = change.new_value
        return f"tick {ev['what']} {self.label_of(change)} ({change.new_value})"

    def tick_put_back(self, ev, i):
        change = self.change_of(ev["label"])
        rows = [row for row, of in self.links().items() if change is not None and of in change.ids]
        put = next((one for one in self.found() if rows and rows[-1] in one.ids), None)
        if put is None:
            return None
        if put.ids[0] not in self.labels.values():
            self.labels[f"q{i}"] = put.ids[0]
        return self.tick({"what": ev["what"], "label": self.label_of(put)}, i)

    def handled(self, label: str):
        """The change of the label as the section "Afgehandeld" offers it: entered, seen or dropped."""
        change = self.change_of(label)
        return change if change is not None and change.state in ("entered", "seen", "dropped") else None

    def put_back(self, ev, i):
        change = self.handled(ev["label"])
        if change is None:
            return None
        files, found, rows_before = self.files(), self.found(), {r.entry_id for r in hand_list.prepared_rows(self.prepared, hand_list.KINDS)}
        note = hand_list.take_back(self.deps, change, self.now, "nl")
        key = self.key(note)
        for k, row in enumerate(r for r in hand_list.prepared_rows(self.prepared, hand_list.KINDS) if r.entry_id not in rows_before):
            self.labels[f"p{i}" + (f"_{k}" if k else "")] = row.entry_id
        self.last = (key, self.check_note(change, key, note, files, found))
        self.check_no_refusal_when_put_back(change, key, note)
        return f"put back {self.label_of(change)} ({change.state}) -> {key}"

    def check_note(self, change, key: str, note: str, files: dict, found: list) -> str | None:
        """Check the note of a press on the change (see the module docstring); the label of the change a refusal names, if any."""
        if key.startswith("?"):
            self.fail(f"a note the test does not know: {note}")
        if key == "hand_put_back_lost":
            self.fail(f"the researcher note for {self.label_of(change)}")
        if key in SILENT and self.files() != files and not (key in ("hand_put_back_on_list", "hand_reopen_not")
                                                             and self.recipe_link_only(files)):
            self.fail(f"{key}, but files changed")
        links = self.links()
        mine = [row for row, of in links.items() if of in change.ids]
        put = next((one for one in self.found() if mine and mine[-1] in one.ids), None)
        if key == "hand_put_back_listed":
            if put is None or put.state != "open":
                self.fail(f"listed, but the put-back is {put and put.state}")
            if note != NL[key].format(row=hand_list.label_of(put, self.deps.store, self.cases_root, "nl")) + self.other_before(change, put, found):
                self.fail(f"listed names another row, or says another value from before: {note}")
            self.accepted.add(put.ids[0])
        if key == "hand_put_back_on_list":
            if put is None or put.state != "open":
                self.fail(f"on the list already, but the put-back is {put and put.state}")
            self.accepted.add(put.ids[0])
        rows = {row.entry_id: row for row in hand_list.prepared_rows(self.prepared, hand_list.KINDS)}
        old = next((rows[x].old_value for x in reversed(change.marked_ids) if x in rows and rows[x].old_value is not None), None)
        if (key == "hand_put_back_done" and not same(self.exact, old) and not (put is not None and set(put.ids) & self.entered())
                and not self.recipe_out_of_step()):
            self.fail(f"put back already, while Exact holds {self.exact}, the old value is {old}, and no put-back of it was entered "
                      f"(put-back {put and put.state})")
        if key in REFUSALS:
            newer = hand_list._newer(change, found)
            want = ("open",) if key in ("hand_put_back_newer_open", "hand_put_back_not_needed") else ("entered", "seen")
            if newer is None or newer.state not in want:
                self.fail(f"{key} names no change in the state it says: {note}")
            if note != NL[key].format(row=hand_list.label_of(newer, self.deps.store, self.cases_root, "nl")):
                self.fail(f"{key} names another change than the newer one: {note}")
            if key == "hand_put_back_not_needed" and not same(newer.new_value, old):
                self.fail(f"not needed, but the change named asks {newer.new_value}, not the old value {old}")
            never = bool(newer.marked_ids) and rows[newer.marked_ids[-1]].rule_id == UNDO_RULE_ID    # its entry to put back is an undo
            if key == "hand_put_back_newer_never" and not never:
                self.fail(f"never, but the change named is no undo that the aid cannot put back: {note}")
            if key == "hand_put_back_newer_entered" and never:
                self.fail(f"the change named can never be put back, and the note asks to put it back (K23): {note}")
            if key == "hand_put_back_newer_open" and same(newer.new_value, old):
                self.fail(f"the change named asks the old value, and the note asks to drop it (K21): {note}")
            again = {"t": "put_back", "label": self.label_of(change), "refused": key, "named": self.label_of(newer)}
            if key == "hand_put_back_newer_open":
                self.pending = [{"t": "tick", "what": "dropped", "label": self.label_of(newer)}, again]
            elif key == "hand_put_back_not_needed":
                self.pending = [{"t": "tick", "what": "entered", "label": self.label_of(newer)}, again]
            elif key == "hand_put_back_newer_entered":
                self.pending = [{"t": "put_back", "label": self.label_of(newer)},
                                {"t": "tick_put_back", "what": "entered", "label": self.label_of(newer)}, again]
            return self.label_of(newer)
        return None

    def check_no_refusal_when_put_back(self, change, key: str, note: str):
        """No refusal, and no "komt niet terug", while Exact holds the change's old value already (K20 b); and "komt niet terug" only
        while something stands in the way of a new put-back (K22): a change on the list, or a newer change whose value Exact holds."""
        rows = {row.entry_id: row for row in hand_list.prepared_rows(self.prepared, hand_list.KINDS)}
        old = next((rows[x].old_value for x in reversed(change.marked_ids) if x in rows and rows[x].old_value is not None), None)
        if key in REFUSALS + ("hand_reopen_not",) and same(self.exact, old) and not self.recipe_out_of_step():
            self.fail(f"{key}, while Exact holds the change's old value already (K20 b): {note}")
        found = self.found()
        at = next(n for n, one in enumerate(found) if set(one.ids) & set(change.ids))
        in_the_way = any(one.state == "open" for one in found) or any(
            one.state in ("entered", "seen") and same(one.new_value, self.exact) and rows[one.ids[-1]].rule_id != UNDO_RULE_ID
            for one in found[at + 1:])
        if key == "hand_reopen_not" and not in_the_way and not self.recipe_out_of_step():
            self.fail(f"the put-back does not return, while nothing stands in the way of a new one (K22): {note}")

    def reopen_(self, ev, i):
        change = self.handled(ev["label"])
        if change is None:
            return None
        files = self.files()
        rows = {row.entry_id: row for row in hand_list.prepared_rows(self.prepared, hand_list.KINDS)}
        state_of = {entry: one.state for one in self.found() for entry in one.ids}
        cases = {rows[entry].case_id for entry in change.ids if rows[entry].rule_id != UNDO_RULE_ID}
        held_undos = [entry for entry, row in rows.items() if row.rule_id == UNDO_RULE_ID and row.case_id in cases
                      and state_of.get(entry) in ("entered", "seen")]          # the undos of its cases that Exact holds
        if change.state == "seen":
            note = NL["hand_reopen_seen"]
        else:
            state = reopen(self.prepared, change.ids, self.now)
            note = NL["hand_reopened"] if state == "open" else NL["hand_reopen_put_back"] if state == "undone" else NL["hand_reopen_not"]
        key = self.key(note)
        if key in SILENT and self.files() != files:
            self.fail(f"{key}, but files changed")
        if key == "hand_reopened":
            after = {entry: one.state for one in self.found() for entry in one.ids}
            if any(after.get(entry) == "undone" for entry in held_undos):
                self.fail("taking the tick back cancelled an undo of its case that was entered or seen")
            tick = next((one for one in reversed(self.ticks) if one[0] & set(change.ids)), None)
            if change.state == "entered" and tick is not None:
                self.ticks.remove(tick)
                if same(self.exact, change.new_value):
                    self.exact = tick[1]
        return f"reopen {self.label_of(change)} ({change.state}) -> {key}"

    def undo_case_(self, ev, i):
        case_id = self.cases.get(ev["case"])
        if case_id is None:
            return None
        made = undo_case(self.cases_root / case_id, PreparedWriter(self.prepared), self.now)
        for k, entry in enumerate(made):
            self.labels[f"u{i}_{k}"] = entry.entry_id
        return f"undo_case {ev['case']} -> {len(made)} rows"

    def step(self, ev: dict, i: int):
        self.now += timedelta(minutes=5)
        act = {"ask": self.ask, "tick": self.tick, "tick_put_back": self.tick_put_back, "put_back": self.put_back,
               "reopen": self.reopen_, "undo_case": self.undo_case_}[ev["t"]]
        self.last = ("", None)
        done = act(ev, i)
        self.trace.append(f"{i:3}: {done if done is not None else '(skipped) ' + str(ev)}")
        if done is not None and ev.get("refused") and self.last == (ev["refused"], ev["named"]) and not self.recipe_undo_open():
            self.fail(f"after following the advice of {ev['refused']} on {ev['named']}, the same refusal again")
        self.check()

    def recipe_out_of_step(self) -> bool:
        """Whether the marks and the model part ways because of an undo made with the recipe (undo_case): the value the marks say
        Exact holds (that of the last entered change of the place) is not the model's, and an undo of the recipe has the mark
        "entered". Such an undo, of a case with no row in the change it belongs to, is passed over while that change's tick is taken
        back and counts again once it is ticked anew, after the buyer entered that change's value: a limit of the recipe that MANUAL
        3.10 names, which the model judges allowed."""
        held = [one.new_value for one in self.found() if one.state in ("entered", "seen")]
        marks = _marks(self.prepared / MARKS_FILE)[0]
        recipe = {entry for label, entry in self.labels.items() if label.startswith("u")}
        return (bool(held) and not same(held[-1], self.exact)
                and any(entry in marks and marks[entry].what == "entered" for entry in recipe))

    def recipe_undo_open(self) -> bool:
        """Whether an undo made with the recipe stands open on the list: K22 makes no put-back again while it does."""
        recipe = {entry for label, entry in self.labels.items() if label.startswith("u")}
        return any(one.state == "open" and set(one.ids) & recipe for one in self.found())

    def judge(self, entry_id: str, found: list):
        """The put-back that judges a prepared write by K19's two clauses with K29 (see the module docstring), or None."""
        owner = next((one for one in found if entry_id in one.ids), None)
        if owner is None:
            return None
        linked = [one for one in found if one.puts_back in owner.ids]
        if linked:
            return linked[-1]
        if owner.state != "replaced":
            return None
        runs = [one for one in found if one.state != "undone"]
        at = runs.index(owner)
        held = [one for one in runs[:at + 1] if one.state in ("entered", "seen")]
        if not held or not same(held[-1].new_value, owner.new_value):
            return None                                  # Exact did not hold the write's value when its row was asked
        value_of = {entry: one.new_value for one in found for entry in one.ids}

        def puts_value_back(one) -> bool:
            return one.puts_back is not None and same(value_of.get(one.puts_back), owner.new_value)
        for one in runs[at + 1:]:
            if one.state in ("entered", "seen") and not same(one.new_value, owner.new_value):
                return one if puts_value_back(one) else None
        return runs[-1] if runs[-1].state == "open" and puts_value_back(runs[-1]) else None

    # --- invariants ---
    def check(self):
        found = self.found()
        open_ = [one for one in found if one.state == "open"]
        if len(open_) > 1:
            self.fail("two open changes at the place")
        links = self.links()
        for one in open_:
            if same(one.new_value, self.exact):
                self.fail(f"an open change asks {one.new_value}, which Exact holds")
            if set(links.values()) & set(one.ids):
                self.fail("an open change has a put-back")
        for entry in self.accepted:
            put = next((one for one in found if entry in one.ids), None)
            if put is None or put.state in ("undone", "already"):
                self.fail(f"a put-back on the list became {put and put.state}")
        open_ids = {entry for one in open_ for entry in one.ids}
        undo_ids = {row.entry_id for row in hand_list.prepared_rows(self.prepared, hand_list.KINDS) if row.rule_id == UNDO_RULE_ID}
        for case_id in self.cases.values():
            writes = [entry for entry in WriteLog(self.cases_root / case_id).entries() if entry.undo_of is None and entry.status == "prepared"]
            if not writes:
                continue
            case = load_case(self.cases_root, case_id)
            _, undone, waiting = _close_history(case, self.prepared)
            for write in writes:
                put = self.judge(write.entry_id, found)
                if put is not None and put.state in ("entered", "seen") and write.entry_id not in undone:
                    self.fail(f"the pre-check keeps a write whose put-back is {put.state}")
                if put is not None and put.state == "open" and write.entry_id not in undone | waiting:
                    self.fail("the pre-check keeps a write whose put-back waits, without saying so")
                if write.entry_id in waiting and not open_ids & undo_ids:
                    self.fail("the pre-check says a put-back waits, and no undo row is open")
            for name in (PREPARED_FILE, MARKS_FILE):
                with held(name):
                    _, undone, waiting = _close_history(case, self.prepared)
                if {write.entry_id for write in writes} - undone or waiting:
                    self.fail(f"with {name} unreadable, the pre-check counts a prepared write as made or waiting")


def run(events: list[dict], folder: Path) -> World:
    world = World(folder)
    for i, ev in enumerate(events):
        world.step(dict(ev), i)
    return world


def choose(rng: random.Random, world: World, i: int) -> dict:
    if world.pending and rng.random() < 0.85:
        return world.pending.pop(0)
    world.pending = []
    found = world.found()
    open_ = [one for one in found if one.state == "open"]
    handled = [one for one in found if one.state in ("entered", "seen", "dropped")]
    options = [("ask", 3.0 if len(world.cases) < 5 else 0.8)]
    if open_:
        options += [("entered", 4.0), ("dropped", 1.5)]
    if handled:
        options += [("put_back", 3.0), ("reopen", 1.5)]
    if world.cases:
        options.append(("undo_case", 0.3))
    pick = rng.uniform(0, sum(weight for _, weight in options))
    for name, weight in options:
        pick -= weight
        if pick <= 0:
            break
    if name == "ask":
        old = rng.choice([world.exact, "12.50", world.exact])
        ev = {"t": "ask", "old": old, "new": rng.choice([value for value in VALUES if not same(value, old)])}
        if world.cases and rng.random() < 0.15:
            ev["case"] = rng.choice(list(world.cases))
        return ev
    if name in ("entered", "dropped"):
        return {"t": "tick", "what": name, "label": world.label_of(rng.choice(open_))}
    if name in ("put_back", "reopen"):
        return {"t": name, "label": world.label_of(rng.choice(handled))}
    return {"t": "undo_case", "case": rng.choice(list(world.cases))}


@pytest.mark.parametrize("name", sorted(MINIMAL))
def test_the_checkers_shortest_sequences_keep_the_invariants(tmp_path, name):
    run(MINIMAL[name], tmp_path / name)


def test_random_sequences_of_one_place_keep_the_invariants(tmp_path):
    for seed in SEEDS:
        rng = random.Random(seed)
        world = World(tmp_path / f"seed{seed}")
        world.trace.append(f"seed {seed}")
        for i in range(STEPS):
            world.step(choose(rng, world, i), i)
