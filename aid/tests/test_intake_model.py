"""A seeded property test of the lines of the intake file for the files that stay in the watched folder (Task 37's review round, ruling
O15), on the idea of the reviewers' model-based harness: random sequences of files given, replaced, removed, held by another program for
a while, unmovable for a pass and left for weeks, driven through the runner's scan step (_scan of aid/runner.py with what the loop keeps,
as _pass calls it) on the real scan, PO store and disk, beside a small model of the rules of the intake file.

The files. A file of the watched folder is a PO PDF (filed the first time its order comes, received before after that), a broken PDF
(an error: it stays), a Word file (skipped, and moved), or an e-mail with a confirmation and a broken attachment (it stays; the
confirmation opens its case the first time, and is received before after that). A PDF can be held by another program, so that its
content cannot even be read (an error that stays), and any file can be unmovable for one pass.

The rules the model holds the lines of each pass against, written out from the rulings:
- each document a pass handles has a line for its outcome; but while its file stays, a line equal to the last line of its document is
  not written again, a file whose content cannot be read counting as the same file until it can be read (O1), and 'received before'
  is kept back after a line that told a filing (O2);
- a file with another content under the same name is another file, and so is a file that left the folder and came back;
- a file that was handled and cannot be moved keeps the line of what it became (O7);
- a file that stays and is still an error at a pass STAY_DAYS days or more after the pass that wrote its first line as a staying file
  is moved to processed/ with one line, the whole days since then (O8); when it cannot be moved then it waits for the next pass.
After every pass the lines it wrote, their time, the files left in the folder and the record of the state are held against the model.

The seeds are this test's own; the whole runs in a few seconds.
"""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from aid.decider import NoDecider
from aid.execution.writer import PreparedWriter
from aid.pipeline import Deps, read_with_parser
from aid.po_store import PoStore
from aid.review.intake import INTAKE_FILE
from aid.runner import STAY_DAYS, _Intake, _load_state, _scan
from aid.tests.conftest import SETTINGS

START = datetime(2026, 11, 12, 9, 0)
SEEDS = range(4100, 4116)
STEPS = 24
FILINGS = ("po", "export", "case", "guess", "answer", "bounce", "auto", "own", "recheck")
PDFS = {"po3": "250103", "po4": "250104", "bad1": None, "bad2": None}       # the contents of a .pdf, with the PO they hold
MAILS = {"mail1": "250101", "mail2": "250102"}                            # the contents of the e-mail, with the PO of its confirmation
NAMES = {"a.pdf": tuple(PDFS), "b.pdf": tuple(PDFS), "m.eml": tuple(MAILS), "n.docx": ("docx",)}
TYPE = ("skipped", "unsupported file type: .docx")


_MADE: dict[tuple[Path, str], bytes] = {}


def made_up(madeup_out: Path, content: str) -> bytes:
    """The bytes of a content, the same bytes each time: a made-up PO PDF, a broken PDF, a Word file, or an e-mail with a confirmation
    and a broken attachment (made once, as an e-mail made again has another boundary between its parts, and so another content)."""
    if (madeup_out, content) not in _MADE:
        _MADE[madeup_out, content] = _made(madeup_out, content)
    return _MADE[madeup_out, content]


def _made(madeup_out: Path, content: str) -> bytes:
    if content.startswith("po"):
        return (madeup_out / f"MU0{content[2]}_po_v1.pdf").read_bytes()
    if content.startswith("bad"):
        return f"not a PDF, made-up {content}".encode()
    if content == "docx":
        return b"made-up"
    from email.message import EmailMessage
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = "sales@s01.example.com", "aid@example.com", "Orderbevestiging"
    mail.set_content("Bijgaand onze orderbevestiging.")
    mail.add_attachment((madeup_out / f"MU0{content[4]}_conf.pdf").read_bytes(), maintype="application", subtype="pdf",
                        filename="bevestiging.pdf")
    mail.add_attachment(b"not a PDF either", maintype="application", subtype="pdf", filename="kapot.pdf")
    return bytes(mail)


class World:
    """The watched folder, the runner's scan step and what its loop keeps, with the model beside them."""

    def __init__(self, folder: Path, madeup_out: Path):
        self.madeup_out, self.now = madeup_out, START
        self.inbox, self.root = folder / "inbox", folder / "cases"
        self.inbox.mkdir(parents=True)
        self.root.mkdir()                                 # as _pass makes it before the scan
        settings = SETTINGS.model_copy(update={"cases_root": self.root, "watched_folder": self.inbox})
        self.deps = Deps(settings=settings, store=PoStore(folder / "store"), reader=read_with_parser, decider=NoDecider(),
                         writer=PreparedWriter(folder), mailer=None, clock=lambda: self.now)
        self.intake, self.failed = _Intake(), set()       # what the loop keeps
        self.held: set[str] = set()                       # the names whose content another program holds
        self.unmovable: set[str] = set()                  # the names that cannot be moved in the next pass
        self.folder: dict[str, str] = {}                  # the model: each file of the folder with its content
        self.filed: set[str] = set()                      # the POs the store holds
        self.cases: set[str] = set()                      # the POs whose confirmation has its case
        self.record: dict[str, dict] = {}                 # each file that stays: {"content", "since", "last": {label: (outcome, detail)}}
        self.written = 0                                  # the lines of the intake file held so far
        self.trace: list[str] = []

    def fail(self, what: str) -> None:
        raise AssertionError(f"{what}\n" + "\n".join(self.trace))

    def step(self, op: tuple) -> None:
        self.trace.append(repr(op))
        kind = op[0]
        if kind == "give":
            _, name, content = op
            (self.inbox / name).write_bytes(made_up(self.madeup_out, content))
            self.folder[name] = content
        elif kind == "remove":
            (self.inbox / op[1]).unlink()
            del self.folder[op[1]]
        elif kind == "hold":
            self.held.add(op[1])
        elif kind == "free":
            self.held.discard(op[1])
        elif kind == "unmovable":
            self.unmovable.add(op[1])
        else:
            self.now += op[1]
            self.scan()
            self.unmovable.clear()

    def scan(self) -> None:
        expected = self.predict()
        _scan(self.deps, self.root, _load_state(self.root, self.now), self.now, self.failed, self.intake)
        path = self.root / INTAKE_FILE
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        new, self.written = lines[self.written:], len(lines)
        if [(line["file"], line["outcome"], line["detail"]) for line in new] != expected:
            self.fail(f"lines {[(line['file'], line['outcome'], line['detail']) for line in new]}, the model {expected}")
        if any(line["time"] != self.now.isoformat(timespec="seconds") for line in new):
            self.fail("a line with another time than its pass")
        if sorted(path.name for path in self.inbox.iterdir() if path.is_file()) != sorted(self.folder):
            self.fail(f"the folder holds {sorted(path.name for path in self.inbox.iterdir() if path.is_file())}, the model {sorted(self.folder)}")
        state = json.loads((self.root / "digest_state.json").read_text(encoding="utf-8")).get("intake", {})
        if {name: record["since"] for name, record in state.items()} != {name: record["since"].isoformat() for name, record in self.record.items()}:
            self.fail(f"the record names {sorted(state)}, the model {sorted(self.record)}")

    def predict(self) -> list[tuple[str, str, str]]:
        """The lines of the pass at self.now by the rules of the module docstring, the model brought up to date."""
        lines, record = [], {}
        for name in sorted(self.folder):
            content = self.folder[name]
            here = None if name in self.held else content            # what the runner reads of its content
            if name in self.held:
                events, stays = [(name, "error", "")], True
            elif content in PDFS and PDFS[content] is not None:
                events, stays = [(name, *self.filing(PDFS[content]))], False
            elif content in PDFS:
                events, stays = [(name, "error", "")], True
            elif content == "docx":
                events, stays = [(name, *TYPE)], False
            else:
                events, stays = [(f"{name}/bevestiging.pdf", *self.case(MAILS[content])), (f"{name}/kapot.pdf", "error", "")], True
            stays = stays or name in self.unmovable                  # handled, and its move refused: it stays without a line of its own
            old = self.record.get(name)
            same = old is not None and (old["content"] is None or here is None or here == old["content"])
            last = dict(old["last"]) if same else {}
            for label, outcome, detail in events:
                before = last.get(label)
                if before == (outcome, detail) or (before is not None and (outcome, detail) == ("skipped", "duplicate") and before[0] in FILINGS):
                    continue
                lines.append((label, outcome, detail))
                last[label] = (outcome, detail)
            if not stays:
                del self.folder[name]
                continue
            since = old["since"] if same else self.now
            if same and any(outcome == "error" for _, outcome, _ in events) and self.now - since >= timedelta(days=STAY_DAYS):
                if name not in self.unmovable:
                    lines.append((name, "set_aside", str((self.now - since).days)))
                    del self.folder[name]
                    continue
            record[name] = {"content": here if here is not None else old["content"] if same else None, "since": since, "last": last}
        self.record = record
        return lines

    def case(self, number: str) -> tuple[str, str]:
        """The outcome of the confirmation of the PO: its case opened the first time, at the time of the pass, received before after
        that."""
        if number in self.cases:
            return "skipped", "duplicate"
        self.cases.add(number)
        return "case", f"{number}_{self.now:%Y%m%dT%H%M%S}"

    def filing(self, number: str) -> tuple[str, str]:
        """The outcome of a PO PDF of the PO: filed the first time, received before after that."""
        if number in self.filed:
            return "skipped", "duplicate"
        self.filed.add(number)
        return "po", f"{number} v1"


def choose(rng: random.Random, world: World) -> tuple:
    options = [("give", 4.0), ("pass", 5.0), ("weeks", 1.2), ("hold", 1.0), ("free", 1.5), ("unmovable", 0.8), ("remove", 0.5)]
    pick = rng.uniform(0, sum(weight for _, weight in options))
    for kind, weight in options:
        pick -= weight
        if pick <= 0:
            break
    pdfs = [name for name in ("a.pdf", "b.pdf") if name in world.folder]
    if kind == "give":
        name = rng.choice(list(NAMES))
        return "give", name, rng.choice(NAMES[name])
    if kind == "hold" and pdfs:
        return "hold", rng.choice(pdfs)
    if kind == "free" and world.held:
        return "free", rng.choice(sorted(world.held))
    if kind == "unmovable" and world.folder:
        return "unmovable", rng.choice(sorted(world.folder))
    if kind == "remove" and world.folder:
        return "remove", rng.choice(sorted(world.folder))
    if kind == "weeks":
        return "pass", timedelta(days=rng.choice([10, 20, 29, 30, 31]))
    return "pass", timedelta(minutes=1)


def test_random_sequences_of_files_that_stay_keep_the_rules_of_the_intake_file(tmp_path, madeup_out, monkeypatch):
    import aid.runner
    import aid.sources.mailbox
    worlds: list[World] = []
    real_sha, real_rename = aid.runner.sha256_file, Path.rename

    def sha256_file(path):
        path = Path(path)
        if worlds and path.parent == worlds[-1].inbox and path.name in worlds[-1].held:
            raise PermissionError(13, "made-up: the file is in use by another program")
        return real_sha(path)

    def rename(self, target):
        if worlds and self.parent == worlds[-1].inbox and self.name in worlds[-1].unmovable:
            raise PermissionError(13, "made-up: the file is in use by another program")
        return real_rename(self, target)
    monkeypatch.setattr(aid.runner, "sha256_file", sha256_file)
    monkeypatch.setattr(aid.sources.mailbox, "sha256_file", sha256_file)
    monkeypatch.setattr(Path, "rename", rename)
    for seed in SEEDS:
        rng = random.Random(seed)
        world = World(tmp_path / f"seed{seed}", madeup_out)
        worlds.append(world)
        world.trace.append(f"seed {seed}")
        for _ in range(STEPS):
            world.step(choose(rng, world))
        world.step(("pass", timedelta(minutes=1)))
