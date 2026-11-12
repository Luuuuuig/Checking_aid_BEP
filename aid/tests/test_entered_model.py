"""A seeded property test of the values entered in Exact as they are laid over a version of a purchase order (Task 43, amendment 8),
on the idea of aid/tests/test_hand_list_model.py: random sequences of events driven through the real code (the prepared writer and
the write log as a case uses them, by_hand's mark, reopen and reconcile, hand_list.take_back, the files of the PO store) beside a
small model of what Exact holds for each line by the marks.

The world. One purchase order with two articles, each with a price, a delivery date and a quantity. The store holds PDF versions,
which number the lines 1 and 2, and looks from exports, which hold the lines in the other order and number them 10 and 20, so that a
case compared with a look asks its change at another place of the list than a case compared with a PDF version, and a value has to
find its line by the article. A case is a supplier's own or made from a try-out, and was compared with one version or look, which
its step 01 holds. From the start the list also holds an entered change of the same line of another order, and an entered article
price, which never count.

The events: a case prepares a change of one value, from the value it was compared with (the version with the model's values laid
over it, as the pipeline compares) to another; the buyer ticks an open change as entered, or drops it; he takes a tick or a drop
back; he puts an entered change back ("Terugdraaien in Exact", whatever the tab answers) and may tick the put-back; and a new PDF
version or a new look is filed, showing what the model says Exact holds or other values, after which the runner's pass compares the
open changes with the order as it stands (reconcile), which may mark changes as seen, and ends each tick of a supplier's change that
the order as it stands, filed after the tick, contradicts (the mark "reopened"). Now and then the buyer ticks a change in the
very second in which a print is filed: that print is taken as it is.

The model reads nothing but the lines that the events add to marks.jsonl, and the rows of prepared_changes.csv by their own columns:
an entry marked entered or seen holds the new value of its row from the time of the mark on, until that mark is taken back; a
drop holds nothing. What Exact holds for a value of an article is what the entry marked last says, among the rows of that value
under either numbering: for a supplier's own case among the rows of supplier's cases alone, for a case made from a try-out among
all of them.

The invariant, after every event, for every stored version and look, each as a supplier's case and as a try-out's case would be
compared with it: as_entered gives, for each value of each line, the model's value when the model holds one whose mark was made
after the version was filed, and else the version's own value; its record names exactly the values that differ from the version's
own, with the time of the mark and who made it; a version of which nothing differs is returned itself; and entered_values holds
no change of a try-out for a supplier's case. The seeds are fixed, and the whole runs in a few seconds.
"""
import csv
import json
import os
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from aid.case import open_case
from aid.decider import NoDecider
from aid.execution.by_hand import FOLDER, LINE_KINDS, MARKS_FILE, as_entered, changes, entered_values, filed_of, mark, reconcile, reopen
from aid.execution.ops import WriteOp
from aid.execution.writer import PREPARED_FILE, PreparedWriter, WriteLog
from aid.madeup.generate import BOLD, Row, Text, pdf_bytes
from aid.pipeline import Deps, read_with_parser
from aid.po_store import PoStore, PoVersion
from aid.review import hand_list
from aid.schema import DocLine, DocValue, Document
from aid.tests.conftest import SETTINGS
from aid.tryout_mark import FIRST_ROW

PO, START = "250108", datetime(2026, 11, 12, 9, 0)
ARTICLES = ("KM-0801", "KM-0802")
FIELDS = {"po_line_price": "unit_price", "po_line_date": "delivery_date", "po_line_quantity": "quantity"}     # a kind, and where a line holds it
NAMES = {"po_line_price": "price", "po_line_date": "delivery_date", "po_line_quantity": "quantity"}           # as a record names it
VALUES = {("KM-0801", "po_line_price"): ["12.50", "12.60", "12.70", "12.90"], ("KM-0802", "po_line_price"): ["0.42", "0.45", "0.47"],
          ("KM-0801", "po_line_date"): ["2026-11-16", "2026-11-20", "2026-11-27"], ("KM-0802", "po_line_date"): ["2026-11-16", "2026-12-04"],
          ("KM-0801", "po_line_quantity"): ["3", "5", "8"], ("KM-0802", "po_line_quantity"): ["50", "60"]}
LINE_OF = {"v": {"KM-0801": "1", "KM-0802": "2"}, "e": {"KM-0802": "10", "KM-0801": "20"}}      # a PDF's numbering, and an export's
ARTICLE_OF = {line_id: article for numbering in LINE_OF.values() for article, line_id in numbering.items()}
SEEDS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233]
STEPS, MOST = 28, 5                                  # the events of a sequence, and the most versions and looks a store gets


class Broken(AssertionError):
    """An invariant that does not hold, with the trace of the sequence."""


class Held(NamedTuple):
    """What the model says Exact holds for one value: the value, and the mark that says so."""
    value: str
    at: str                                          # the time of the mark, as marks.jsonl writes it
    by: str
    order: int                                       # the line of the mark in marks.jsonl


class World:
    """One folder with its store, its list and its cases, and the model of the marks."""

    def __init__(self, folder: Path, tryout_document: Path):
        shutil.rmtree(folder, ignore_errors=True)
        self.cases_root, self.store, self.document = folder / "cases", PoStore(folder / "store"), tryout_document
        self.prepared = self.cases_root / FOLDER
        self.now = START
        self.deps = Deps(settings=SETTINGS.model_copy(update={"cases_root": self.cases_root}), store=self.store, reader=read_with_parser,
                         decider=NoDecider(), writer=PreparedWriter(self.prepared), mailer=None, clock=lambda: self.now)
        self.looks: list[str] = []                   # the folders of the store, in the order they were filed: "v1", "e1", ...
        self.cases: dict[tuple[bool, str], tuple[str, int]] = {}     # (made from a try-out, the look compared with) -> the case id,
                                                     # and the number that look was given with when the case was compared with it
        self.made_up: set[str] = set()               # the ids of the cases made from a try-out
        self.marked: dict[str, tuple[int, str, str]] = {}    # entry id -> the line, the time and the maker of its mark entered or seen
        self.read = 0                                # the lines of marks.jsonl the model has read
        self.trace: list[str] = []
        self.file("v", {key: values[0] for key, values in VALUES.items()})
        others = [WriteOp(kind="po_line_price", po_number="250111", version=1, line_id="1", old_value="12.50", new_value="99.90"),
                  WriteOp(kind="article_price", po_number=PO, version=1, article_code=ARTICLES[0], new_value="99.90")]
        self.now += timedelta(minutes=1)             # marked after the first version was filed, so that the time does not keep them out
        mark(self.prepared, [PreparedWriter(self.prepared).apply(op, "250111_20261101T090000", "R04", {}, self.now).entry_id for op in others],
             "entered", self.now)                    # another order's line 1, and an article price: neither is a value of this order
        self.now += timedelta(minutes=1)
        self.follow()

    # --- the store ---
    def file(self, kind: str, values: dict[tuple[str, str], str]) -> str:
        """A PDF version ("v") or a look from an export ("e") with these values, filed now as the store keeps one: its po.json."""
        name = f"{kind}{sum(1 for look in self.looks if look.startswith(kind)) + 1}"
        lines = [DocLine(line_id=line_id, item_code=DocValue(value=article, text=article), price_basis=DocValue(value="1", text="1"),
                         **{field: DocValue(value=values[(article, one)], text=values[(article, one)]) for one, field in FIELDS.items()})
                 for article, line_id in LINE_OF[kind].items()]
        folder = self.store.root / PO / name
        version = PoVersion(po_number=PO, version=int(name[1:]) if kind == "v" else 1, version_date=None,
                            pdf_path=folder / "po.pdf" if kind == "v" else folder / "export.csv", supplier="S08", total=None, above_authorization=None,
                            document=Document(kind="po", po_reference=DocValue(value=PO, text=PO), lines=lines))
        folder.mkdir(parents=True)
        (folder / "po.json").write_text(version.model_dump_json(indent=1) + "\n", encoding="utf-8")
        os.utime(folder / "po.json", (self.now.timestamp(), self.now.timestamp()))
        self.looks.append(name)
        return name

    def stored(self, name: str) -> PoVersion:
        """The version or the look of that folder, as the store gives it now."""
        given = self.store.versions(PO) if name.startswith("v") else self.store.snapshots(PO)
        return given[int(name[1:]) - 1]

    def own(self, version: PoVersion) -> dict[tuple[str, str], str]:
        """The values of a version, by article and kind."""
        return {(line.item_code.value, kind): getattr(line, field).value for line in version.document.lines for kind, field in FIELDS.items()}

    # --- the model ---
    def rows(self) -> dict[str, dict]:
        with open(self.prepared / PREPARED_FILE, encoding="utf-8", newline="") as f:
            return {row["entry_id"]: row for row in csv.DictReader(f)}

    def follow(self) -> None:
        """Read the lines that the last event added to marks.jsonl: an entry marked entered or seen holds its row's value from then
        on, and a tick taken back or a drop holds nothing."""
        path = self.prepared / MARKS_FILE
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for number, line in enumerate(lines[self.read:], self.read):
            record = json.loads(line)
            if record["mark"] in ("entered", "seen"):
                self.marked[record["entry_id"]] = (number, record["at"], record["by"])
            elif record["mark"] in ("reopened", "dropped", "replaced"):
                self.marked.pop(record["entry_id"], None)
        self.read = len(lines)

    def holds(self, tryout: bool) -> dict[tuple[str, str], Held]:
        """What Exact holds by the marks that count in a supplier's case, or with tryout in a case made from a try-out: for each value
        of an article that a mark says anything about, the new value of the entry marked last."""
        rows = self.rows() if self.marked else {}
        said: dict[tuple[str, str], Held] = {}
        for entry_id, (order, at, by) in sorted(self.marked.items(), key=lambda item: item[1][0]):
            row = rows[entry_id]
            if row["po_number"] == PO and row["kind"] in LINE_KINDS and (tryout or row["case_id"] not in self.made_up):
                said[(ARTICLE_OF[row["line_id"]], row["kind"])] = Held(row["new_value"], at, by, order)
        return said

    def compared(self, name: str, tryout: bool) -> tuple[dict[tuple[str, str], str], list[dict]]:
        """What the model says a case is compared with for that look: its values with those laid over that Exact holds by a mark made
        after the look was filed, and the record of the values replaced, in the order of the look's lines."""
        return self.laid(self.stored(name), self.store.filed_at(PO, name).timestamp(), self.holds(tryout))

    def laid(self, version: PoVersion, filed: float, said: dict[tuple[str, str], Held]) -> tuple[dict[tuple[str, str], str], list[dict]]:
        """compared(), for a version filed at that time and what Exact holds."""
        values, replaced = self.own(version), []
        for line in version.document.lines:
            for kind in LINE_KINDS:
                key = (line.item_code.value, kind)
                held = said.get(key)
                if held is not None and datetime.fromisoformat(held.at).timestamp() > filed and held.value != values[key]:
                    replaced.append({"line_id": line.line_id, "field": NAMES[kind], "was": values[key], "entered": held.value,
                                     "marked_at": held.at, "by": held.by})
                    values[key] = held.value
        return values, replaced

    # --- the events ---
    def case(self, tryout: bool, name: str) -> tuple[str, int]:
        """The case of that kind that was compared with that look, opened at its first change, its step 01 holding the look as the
        store gave it then: its id, and the version number of that look in its step 01, which its changes carry (a look from an
        export is given with the number of the latest PDF version of the day)."""
        if (tryout, name) not in self.cases:
            made = open_case(self.cases_root, "confirmation", [self.document] if tryout else [], PO, self.now + timedelta(seconds=len(self.cases)))
            compared = self.stored(name)
            made.write_step(1, "po_versions", {"po_number": PO, "versions": [compared.model_dump(mode="json")]})
            self.cases[(tryout, name)] = (made.case_id, compared.version)
            if tryout:
                self.made_up.add(made.case_id)
        return self.cases[(tryout, name)]

    def ask(self, rng: random.Random) -> str:
        tryout, name = rng.random() < 0.35, rng.choice(self.looks)
        article, kind = rng.choice(ARTICLES), rng.choice(LINE_KINDS)
        old = self.compared(name, tryout)[0][(article, kind)]
        new = rng.choice([value for value in VALUES[(article, kind)] if value != old])
        case_id, version = self.case(tryout, name)
        op = WriteOp(kind=kind, po_number=PO, version=version, line_id=LINE_OF[name[0]][article], old_value=old, new_value=new)
        entry = PreparedWriter(self.prepared).apply(op, case_id, "R04", {}, self.now)
        WriteLog(self.cases_root / case_id).append(entry)
        return f"ask {'try-out' if tryout else 'supplier'} {name} {article} {kind} {old} -> {new}"

    def found(self) -> list:
        """The changes of the order's lines, as the tab would show them; the other order's change and the article price stay as they
        are."""
        found, passed = changes(self.prepared)
        assert passed == 0
        return [change for change in found if change.po_number == PO and change.kind in LINE_KINDS]

    def tick(self, rng: random.Random, what: str) -> str | None:
        open_ = [change for change in self.found() if change.state == "open"]
        if not open_:
            return None
        change = rng.choice(open_)
        if mark(self.prepared, change.ids, what, self.now) == 0:
            self.fail("a mark of an open change wrote nothing")
        return f"{what} {change.kind} line {change.line_id} {change.old_value} -> {change.new_value}{' (try-out)' if change.tryout else ''}"

    def take_back(self, rng: random.Random) -> str | None:
        handled = [change for change in self.found() if change.state in ("entered", "dropped")]
        if not handled:
            return None
        change = rng.choice(handled)
        return f"reopen {change.kind} line {change.line_id} ({change.state}) -> {reopen(self.prepared, change.ids, self.now)}"

    def put_back(self, rng: random.Random) -> str | None:
        held = [change for change in self.found() if change.state in ("entered", "seen")]
        if not held:
            return None
        change = rng.choice(held)
        hand_list.take_back(self.deps, change, self.now, "nl")
        return f"put back {change.kind} line {change.line_id} {change.old_value} -> {change.new_value}"

    def print(self, rng: random.Random) -> str | None:
        """A new PDF version or a new look, and the pass of the runner after it. It shows what the model says Exact holds; or, one
        time in two, also the new value of an open change, as when the buyer entered it without a tick; or one value that is
        another altogether."""
        if len(self.looks) >= MOST:
            return None
        values = self.own(self.stored(self.looks[0])) | {key: held.value for key, held in self.holds(False).items()}
        open_ = [change for change in self.found() if change.state == "open"]
        if open_ and rng.random() < 0.5:
            change = rng.choice(open_)
            values[(ARTICLE_OF[change.line_id], change.kind)] = change.new_value
        elif rng.random() < 0.4:
            key = rng.choice(list(VALUES))
            values[key] = rng.choice(VALUES[key])
        name = self.file(rng.choice("ve"), values)
        ticked = ""
        if open_ and rng.random() < 0.3:             # a tick in the second of the filing: not after it
            change = rng.choice(open_)
            mark(self.prepared, change.ids, "entered", self.now)
            ticked = f", ticked {change.kind} line {change.line_id} -> {change.new_value} in that second"
        self.now += timedelta(minutes=1)
        seen = reconcile(self.prepared, self.store, self.cases_root, self.now)
        return f"filed {name}{ticked}, {seen} seen"

    def step(self, rng: random.Random, number: int) -> None:
        self.now += timedelta(minutes=5)
        events = [(self.ask, 4.0), (lambda r: self.tick(r, "entered"), 4.0), (lambda r: self.tick(r, "dropped"), 0.8), (self.take_back, 1.0),
                  (self.put_back, 1.5), (self.print, 1.2)]
        pick = rng.uniform(0, sum(weight for _, weight in events))
        for event, weight in events:
            pick -= weight
            if pick <= 0:
                break
        done = event(rng)
        self.trace.append(f"{number:3}: {done if done is not None else '(nothing to do)'}")
        self.follow()
        self.check()

    # --- the invariant ---
    def fail(self, what: str):
        raise Broken(f"{what}\n" + "\n".join(self.trace) + "\n" + "\n".join(
            f"   {c.kind} line {c.line_id} {c.old_value}->{c.new_value} {c.state} {'try-out' if c.tryout else ''} {c.marked_at or ''}"
            for c in self.found()))

    def check(self) -> None:
        said = {tryout: self.holds(tryout) for tryout in (False, True)}
        for name in self.looks:
            version, filed = self.stored(name), self.store.filed_at(PO, name)
            for tryout in (False, True):
                values, replaced = self.laid(version, filed.timestamp(), said[tryout])
                laid, record = as_entered(version, self.prepared, filed, None, tryout)
                if self.own(laid) != values:
                    self.fail(f"{name} for a {'try-out' if tryout else 'supplier'}'s case: {self.own(laid)}, the model says {values}")
                if record != replaced:
                    self.fail(f"{name} for a {'try-out' if tryout else 'supplier'}'s case: the record {record}, the model says {replaced}")
                if not replaced and laid is not version:
                    self.fail(f"{name}: nothing was replaced, and the version itself did not come back")
                if replaced and (laid is version or self.own(version) == values):
                    self.fail(f"{name}: values were replaced in the version given")
        if any(one.change.tryout for one in entered_values(self.prepared, PO)):
            self.fail("a change of a try-out counts in a supplier's case")
        # no tick of a supplier's change stands that the order as it stands, filed after it, contradicts (the review round, W9)
        standing = self.store.as_it_stands(PO)
        filed = filed_of(self.store, standing).timestamp()
        for key, held in said[False].items():
            if datetime.fromisoformat(held.at).timestamp() < filed and self.own(standing)[key] != held.value:
                self.fail(f"the tick of {key} ({held.value}, {held.at}) stands, and the order as it stands, filed after it, shows "
                          f"{self.own(standing)[key]}")


def tryout_document(folder: Path) -> Path:
    """A made-up document whose first row is the try-out's (aid/tryout_mark.py): the input of the cases made from a try-out."""
    path = folder / "PROEF orderbevestiging.pdf"
    path.write_bytes(pdf_bytes([[Row([Text(0, FIRST_ROW, BOLD, 14)]), Row([Text(0, "Orderbevestiging")])]], "made-up try-out"))
    return path


def test_random_sequences_of_marks_versions_and_try_outs_keep_the_invariant(tmp_path):
    document = tryout_document(tmp_path)
    marks = 0
    for seed in SEEDS:
        rng = random.Random(seed)
        world = World(tmp_path / f"seed{seed}", document)
        world.trace.append(f"seed {seed}")
        world.check()
        for number in range(STEPS):
            world.step(rng, number)
        marks += world.read
    assert marks > 100                               # the sequences did mark changes: the invariant was not held by empty lists
