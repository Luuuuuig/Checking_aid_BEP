"""The case folder: one folder per case, holding the input files, the output of each step, the
messages, the writes and the audit record.

    <cases_root>/<case_id>/
        case.json         case id, kind, PO number, time of opening, and the input files with their sha256 (this module); and
                          po_found_by, only when the PO number was found otherwise than as the one number of the PO number
                          pattern (aid/sources/mailbox.py), so that the case goes to the buyer (aid/pipeline.py)
        input/            copies of the input files (this module)
        steps/            the JSON output of each step: 01_<name>.json to 08_result.json (aid/pipeline.py)
        messages/         the messages of the case (aid/messaging):
            sent.jsonl        one record per message sent
            replies/          the suppliers' replies, filed by file_reply
            bounces/          the bounces
            auto/             the automatic replies, such as an out-of-office notice
            escalated.jsonl   the questions whose wait ended in review
        write_log.jsonl   one line per write or prepared change (aid/execution/writer.py)
        dispositions.csv  the aid's decisions in the evaluation's form (aid/pipeline.py, record_dispositions)
        decisions.jsonl   the buyer's decisions on the review page (aid/review)
        audit.json        the audit record of the run (aid/audit.py)

The case id is "<PO number>_<time of opening>", or "unknown_<time of opening>" when no PO number is known.

A case.json that cannot be read (torn, not JSON, not the object open_case writes) makes load_case raise ValueError naming the
folder, and find_case_by_hash pass over the folder, so that one damaged folder does not stop the intake of new documents.
"""
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel

CaseKind = Literal["confirmation", "cart"]


@dataclass
class Case:
    case_id: str                 # f"{po_number}_{now:%Y%m%dT%H%M%S}", or f"unknown_{now:%Y%m%dT%H%M%S}"
    root: Path                   # cases_root / case_id, with input/, steps/, messages/
    kind: CaseKind
    po_number: str | None
    input_files: list[Path]      # the copies in input/, in the order they were given
    po_found_by: str | None = None   # how the PO number was found when not as the pattern's one number (case.json); else None

    def write_step(self, n: int, name: str, payload: BaseModel | dict) -> Path:
        """Write the output of a step to steps/{n:02d}_{name}.json and return the path.
        A pydantic model is written as its `model_dump(mode="json")`, a dict as it is."""
        data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        path = self.root / "steps" / f"{n:02d}_{name}.json"
        path.parent.mkdir(exist_ok=True)
        _write_json(path, data)
        return path

    def read_step(self, name: str) -> dict:
        """Return the JSON of the step called name, whatever its number. If the name was written
        under two numbers, the highest number wins."""
        found = []
        for path in (self.root / "steps").glob("*.json"):
            number, _, step_name = path.stem.partition("_")
            if number.isdigit() and step_name == name:
                found.append((int(number), path))
        if not found:
            raise FileNotFoundError(f"case {self.case_id} has no step {name!r}")
        return json.loads(max(found)[1].read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def open_case(cases_root: Path, kind: str, files: list[Path], po_number: str | None, now: datetime, found_by: str | None = None) -> Case:
    """Create the folder of a new case, copy the input files into it and write case.json. found_by says how the PO number was found
    when not as the one number of the PO number pattern (aid/sources/mailbox.py); case.json holds it as po_found_by, and only then.

    Nothing is created when the call is refused. It raises ValueError for a kind that does not exist,
    a PO number that cannot be part of a folder name, or two files with the same name; FileNotFoundError
    for a file that is missing; and FileExistsError when a case with this id exists, which happens when
    the same PO number is opened twice in the same second. An existing case is never touched.
    """
    if kind not in get_args(CaseKind):
        raise ValueError(f"unknown case kind {kind!r}, expected one of {get_args(CaseKind)}")
    po_number = po_number or None
    case_id = f"{po_number or 'unknown'}_{now:%Y%m%dT%H%M%S}"
    _require_folder_name(case_id)
    files = [Path(f) for f in files]
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        raise ValueError(f"two input files have the same name: {names}")
    for f in files:
        if not f.is_file():
            raise FileNotFoundError(f"input file not found: {f}")

    root = Path(cases_root) / case_id
    root.mkdir(parents=True)                       # FileExistsError if the case exists
    for sub in ("input", "steps", "messages"):
        (root / sub).mkdir()
    entries = []
    for f in files:
        copy = root / "input" / f.name
        shutil.copy2(f, copy)                      # copy2 keeps the modification time, the capture date of a saved cart page
        entries.append({"name": copy.name, "sha256": sha256_file(copy), "bytes": copy.stat().st_size})
    meta = {"case_id": case_id, "kind": kind, "po_number": po_number,
            "opened_at": now.isoformat(), "files": entries} | ({"po_found_by": found_by} if found_by is not None else {})
    _write_json(root / "case.json", meta)         # last: a folder without a case.json is not a case
    return _case_from_meta(root, meta)


def find_case_by_hash(cases_root: Path, digest: str) -> Case | None:
    """Return the first case (in folder-name order) that has an input file with this sha256, or None.
    Reads every case.json; anything in cases_root that is not a case is skipped, and so is a folder whose
    case.json cannot be read (load_case names it)."""
    cases_root = Path(cases_root)
    if not cases_root.is_dir():
        return None
    for root in sorted(cases_root.iterdir()):
        if not (root / "case.json").is_file():
            continue
        try:
            meta = _read_meta(root)
        except (OSError, ValueError):
            continue
        if any(f["sha256"] == digest for f in meta["files"]):
            return _case_from_meta(root, meta)
    return None


def load_case(cases_root: Path, case_id: str) -> Case:
    """Load the case <cases_root>/<case_id> from its case.json. A case.json that cannot be read as one raises ValueError naming
    the folder; a folder without one raises FileNotFoundError."""
    _require_folder_name(case_id)
    root = Path(cases_root) / case_id
    return _case_from_meta(root, _read_meta(root))


def _require_folder_name(case_id: str) -> None:
    """A case id is one folder name, so that no id can point outside cases_root."""
    if case_id in ("", ".", "..") or Path(case_id).name != case_id:
        raise ValueError(f"{case_id!r} cannot be the name of a case folder")


def _read_meta(root: Path) -> dict:
    """The content of the folder's case.json, checked for what _case_from_meta reads; ValueError naming the folder otherwise."""
    text = (root / "case.json").read_bytes()
    try:
        meta = json.loads(text.decode("utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("it holds no object")
        for name in ("kind", "po_number", "files"):
            if name not in meta:
                raise ValueError(f"it has no {name}")
        if not isinstance(meta["files"], list) or not all(isinstance(f, dict) and "name" in f and "sha256" in f for f in meta["files"]):
            raise ValueError("its files are not a list of names with their sha256")
    except ValueError as exc:
        raise ValueError(f"the case.json of {root.name} cannot be read: {' '.join(str(exc).split())}") from None
    return meta


def _case_from_meta(root: Path, meta: dict) -> Case:
    """The one place that builds a Case from disk, so that open_case, find_case_by_hash and
    load_case return the same shape. The case id is the folder name, so a link built from it always resolves."""
    return Case(case_id=root.name, root=root, kind=meta["kind"], po_number=meta["po_number"],
                input_files=[root / "input" / f["name"] for f in meta["files"]],
                po_found_by=found if isinstance(found := meta.get("po_found_by"), str) else None)


def _write_json(path: Path, data) -> None:
    """Write data as JSON (UTF-8, one-space indent, final newline). The file appears whole or not at
    all: a Colab session can end at any moment, and every case.json is read when a new document arrives."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# Under cases_root, the folder where a part keeps a copy of a document for a moment, never in the system's temporary folder, which lies
# outside the aid's folders: the runner the attachments of an e-mail it unpacks (aid/sources/mailbox.py), the Copilot reader the PDF
# it reads (aid/readers/copilot_client.py). It lies in the review page's temporary folder, whose start spares it (aid/review/__main__.py);
# the runner empties it at its own start, so that a copy left by a part that was ended in the middle of it never stays (aid/runner.py).
TEMPORARY, WORK = "tmp", "work"


@contextmanager
def work_copy_folder(work: Path, prefix: str) -> Iterator[Path]:
    """A folder of its own in the work folder (<cases_root>/tmp/work) for the time of the block, for a copy of a document, removed
    afterwards with what it holds. The folders made on the way (the work folder, the temporary folder above it, the cases root) go
    again afterwards when they are empty, so that a part leaves no folder of its own behind."""
    work = Path(work)
    made = [folder for folder in (work, work.parent, work.parent.parent) if not folder.exists()]      # the innermost first
    work.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix, dir=work) as folder:
            yield Path(folder)
    finally:
        for folder in made:
            try:
                folder.rmdir()
            except OSError:                      # not empty: the review page's files, or a case
                break
