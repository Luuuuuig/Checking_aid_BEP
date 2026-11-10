"""The record of what the made-up batch writes, for the golden comparison of test_pipeline.py (rule 7 of Task 40): whatever a task
changes, the made-up batch must write the files it wrote before. The record is FIXTURE, aid/tests/fixtures/made_up_batch.json.

What is recorded. The made-up batch is python3 -B -m aid.demo --out <run>, which runs the 22 made-up cases with the parser. For
summary.csv and for every step file (cases/<case>/steps/*.json), disposition log (cases/<case>/dispositions.csv) and write log
(cases/<case>/write_log.jsonl) of the run, the record holds the file's path under the run, with the time in its case folder's name
written <T>, and the sha256 of its content normalised. The parts that differ between two runs of the same code are normalised as
compare_runs.py of the reviews does it:

    an absolute path under the run folder, or under the folder that   <RUN>/... or <REPO>/..., the rest of the path with /
    holds the aid package that ran (the made-up exports)
    a time in a case id or a case folder name (20261110T134900)       <T>
    a timestamp (2026-11-10T13:49:00.368999+00:00)                    <TIME>
    a duration ("seconds")                                             <SECONDS>
    the random id of a write-log entry (32 hex digits)                 <ID>

A JSON file is read and written again with indent 1, and a JSON Lines file line by line, so that neither the line ends nor the marks
between the folders of a path count; a CSV file is read as text.

How to make the record again. It holds what the code of e26bbb7, the code before Task 40, wrote:

    git archive e26bbb7 aid evaluation | tar -x -C <old>
    (cd <old> && python3 -B -m aid.demo --out <run>)
    python3 -B -m aid.tests.made_up_batch <run> <old> e26bbb7        (from the repository root: writes FIXTURE)

A task that changes these files on purpose makes the record again from its own code, in the same commit, and says in the commit's
message why the files changed. Nothing else may change them.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "made_up_batch.json"
CASE_TIME = re.compile(r"(?<![0-9])[0-9]{8}T[0-9]{6}(?![0-9])")
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?")
ENTRY_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")


def record(run: Path, package_root: Path) -> dict[str, str]:
    """The record of the made-up batch run into `run` by the aid package that lies in `package_root`: {the file's path under the run,
    its case time as <T>: the sha256 of its normalised content} (see the module docstring), by path."""
    run, root = Path(run).resolve(), Path(package_root).resolve()
    places = sorted([(run, "<RUN>"), (root, "<REPO>")], key=lambda place: len(place[0].parts), reverse=True)   # the deeper first
    files = [run / "summary.csv", *run.glob("cases/*/steps/*.json"), *run.glob("cases/*/dispositions.csv"),
             *run.glob("cases/*/write_log.jsonl")]
    found = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            content = _json(json.loads(text), places)
        elif path.suffix == ".jsonl":
            content = "\n".join(_json(json.loads(line), places) for line in text.splitlines() if line.strip())
        else:
            content = _text(text, places)
        found[CASE_TIME.sub("<T>", path.relative_to(run).as_posix())] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return dict(sorted(found.items()))


def _json(value, places: list[tuple[Path, str]]) -> str:
    return json.dumps(_walk(value, places), indent=1, ensure_ascii=False)


def _walk(value, places: list[tuple[Path, str]]):
    """The JSON value with each string normalised, a whole path under one of the places written from its word, and each duration
    ("seconds") as <SECONDS>."""
    if isinstance(value, dict):
        return {key: "<SECONDS>" if key == "seconds" and isinstance(item, (int, float)) else _walk(item, places)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, places) for item in value]
    if isinstance(value, str):
        path = Path(value)
        for place, word in places:
            if path.is_absolute() and path.is_relative_to(place):
                value = "/".join([word, *path.relative_to(place).parts])
                break
        return _text(value, places)
    return value


def _text(text: str, places: list[tuple[Path, str]]) -> str:
    """The text with a place written by its word, and the times and the entry ids normalised."""
    for place, word in places:
        text = text.replace(str(place), word)
    text = TIMESTAMP.sub("<TIME>", text)
    text = CASE_TIME.sub("<T>", text)
    return ENTRY_ID.sub("<ID>", text)


def main(argv: list[str]) -> int:
    """Write FIXTURE from a run: python3 -B -m aid.tests.made_up_batch <run> <package root of the code that ran it> <commit>."""
    run, root, commit = Path(argv[0]), Path(argv[1]), argv[2]
    files = record(run, root)
    about = [f"What the made-up batch (python3 -B -m aid.demo) wrote with the code of {commit}: the record of aid/tests/made_up_batch.py,",
             "whose docstring says how to make it again. A task that changes these files on purpose makes it again in the same commit and",
             "says why in the commit's message."]
    FIXTURE.write_text(json.dumps({"about": about, "commit": commit, "files": files}, indent=1) + "\n", encoding="utf-8")
    print(f"{len(files)} files recorded in {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
