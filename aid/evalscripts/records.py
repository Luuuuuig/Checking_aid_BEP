"""The records command of the evaluation: records the evaluation protocol asks for and that no other tool makes.

    python -m aid.evalscripts.records log    --out DIR [--events FILE ...] [--timing FILE ...] [--assignment FILE]
                                             [--case-list FILE --cases-root DIR] [--dispositions FILE ...] [--seq FILE]
                                             [--check-only] [--while-running]
    python -m aid.evalscripts.records sheet  --out DIR ...     (aid/evalscripts/raw_output.py)
    python -m aid.evalscripts.records counts --out DIR ...     (aid/evalscripts/raw_output.py)

The command only reads. It writes its records into the folder of --out and nowhere else: never into a case folder, the cases
root, the PO store, the watched folder or an input file, and it calls no writer of the aid. This module holds the command line,
what the three subcommands share, and the subcommand log; sheet and counts are in raw_output.py, which is imported only when one
of them is asked for.

What the subcommands share.
- expand: a file argument with a star or a question mark is expanded here, as the command line of Windows does not do it.
- check_out: the refusals for the folder of --out, checked on resolved paths before the folder is made. Refused are the cases
  root and every folder in it, a case folder, a cases root and a data folder of the aid (a settings.json beside cases, po_store
  or inbox) and every folder inside one of these, and a folder that holds one of the outputs already; for the log and the sheet
  of real cases, which are records of restricted storage, also a folder inside a clone of a repository. There is no option to
  write over a file: a sheet that a rater filled is never lost to a second run.
- write_all: every file is written under the name <name>.partial and all are renamed together at the end; a failure removes
  what the run wrote, so that no file that looks finished is left and the next run is not refused for it.
- write_manifest: a file beside the outputs that says what they were made from: the subcommand and its options, the day, the
  aid's version, and the base name and sha256 of every input and output file.
- csv_text: a record as CSV text, in UTF-8 with commas and \n line ends on every system.
A refusal is one sentence that names the argument and what to do. The command then ends with exit code 2, through the error of
its argument parser, before anything is written; it ends with 0 when the records are written, whatever they say.

log writes two records, one row per case, and its manifest (log_manifest.json). The cases are those of the assignment, in its
order, then every other case of the timer's exports and of the case list, by its first start and its case id.

timer_check.csv answers the first of the three criteria of protocol section 3 ("The timing procedure works") and keeps the three
apart. log_complete is criterion 1 and nothing else: start, pauses and stop present (timer_log.check). consistent is said beside
it: only steps the timer allows, the order of time, a timing row that agrees with the events, and the condition of the
assignment; it never changes log_complete. boundaries_unambiguous and active_passive_apart are left blank: criteria 2 and 3 are
the researcher's judgement, for which the hints give what can be seen mechanically (an assisted case with a run of the aid and
no wait event; a case the aid closed by its rules, for which the timer's stop is still to settle; a reload or a reopening whose
gap was counted by a choice). The file holds no seconds and no time, so that it can be made and read after every session.

case_log.csv is the log of protocol section 16, in its order. The timer gives the events, the seconds (those of the timing row
that agrees with the events) and the fallback; the assignment gives the band, the analysis band by the sparse-band rule of
protocol 9.2 (analysis_bands), the supplier code, the session, the block and the position; the case list gives the month. With a
case list, the case folders give the columns of the aid (aid/evalscripts/case_runs.py). A run of the aid belongs to the handling
of a case when it started between the first start of the timer and its stop: the attempts, the seconds, the cost and the reader
are summed over those runs only, and the status, the version with its method and the numbers of the unreviewed output are
those of the run that counts among them. A run that started before the handling or after it is counted apart: a development
run of weeks before, or the replay that makes the raw output of a manual case. A run that cannot be placed (its audit record
gives no start time) is counted in neither column and named in the remarks; without a start and a stop of the timer no run can
be placed, and both columns stay blank. For a manual case the columns of the aid stay blank, as nothing of the aid
was shown in its handling. The number of dispositions and the version selected come from the disposition log: the file of the
folders of the runs in the handling, and the logs given with --dispositions. With --seq, six columns of the SEQ record follow.
Text that the researcher typed (the deviations and notes of the timer, the reason and the note of the SEQ record) is written as
"[withheld]" when it names a purchase order, a case folder, a document or a supplier; the remarks name the column. Without a case
list there is nothing to check it against: it is copied, the remarks name its column as not checked, and the terminal says so.

What is withheld until handling is finished. The log is the first file that sets seconds and ratings beside the condition, and
the protocol looks at neither until all timed handling is finished (sections 6.3 and 9.7). With an assignment, the log is not
written while an assigned case has no stop; events that carry a session come from a timer that ran with an assignment and give
no log without it; and --check-only writes timer_check.csv alone, for a run after each session.

The case folders are read with one read of the bytes of each file, and not at all while the aid runs on the data folder, unless
--while-running is given (case_runs.aid_running). The terminal shows no time, no seconds and no path under the cases root.
"""
import argparse
import csv
import glob
import hashlib
import importlib
import io
import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from aid.evalscripts import case_runs, timer_log
from aid.evalscripts.timer_log import Refusal
from aid.pipeline import DISPOSITION_COLUMNS, DISPOSITION_FILE, Deps
from aid.routing import NO_VERSION

PROG = "python -m aid.evalscripts.records"
SUBCOMMANDS = {"log": "the per-case log of protocol section 16 and the check of the timer log of each case",
               "sheet": "the raw_output_scores sheet, filled in advance from the aid's unreviewed output",
               "counts": "the counts of protocol section 8.2 per field, from a sheet"}
PARTIAL = ".partial"                             # what a file is called while the files of a run are not all written
OUTSIDE = "give a new folder outside the aid's data folder"
ALREADY = "{name} is already in {folder}: give a new folder, for example one per day"
HELD = "{name} is open in another program (Excel?): close it and run again"
CASE_FILE = "case.json"                          # what a case folder is told by (aid/case.py)
SETTINGS_FILE = "settings.json"                  # what a data folder is told by, beside one of DATA_FOLDERS (aid/settings.py)
DATA_FOLDERS = ("cases", "po_store", "inbox")
GIT = ".git"                                     # what a clone of a repository is told by: a folder, or a file in a worktree


def today() -> date:
    """The day of the run, from the clock; a function of its own so that a test can fix it."""
    return date.today()


# --- what the three subcommands share ---

def expand(parser: argparse.ArgumentParser, patterns, argument: str = "") -> list[Path]:
    """The files of a file argument: `patterns` is what the command line gave for it (None, one path or several). A path that
    holds a star or a question mark is expanded to the files that match it, in the order of their names; the command line of
    Windows leaves that to the command. A pattern that matches nothing ends the command through `parser`, with a sentence that
    names the argument (`argument`, as "--events") and the pattern without its folder. A path without a wildcard is kept as it
    stands: whether the file is there is said when it is read. No file is given twice. A bracket is no wildcard: it stands in the
    name of a file that was downloaded twice."""
    if patterns is None:
        return []
    if isinstance(patterns, (str, os.PathLike)):
        patterns = [patterns]
    found: dict[Path, None] = {}
    for pattern in patterns:
        text = os.fspath(pattern)
        if "*" in text or "?" in text:
            matches = sorted(glob.glob(text.replace("[", "[[]")))
            if not matches:
                parser.error(f"{argument + ': ' if argument else ''}no file matches {Path(text).name}")
            found.update(dict.fromkeys(Path(match) for match in matches))
        else:
            found[Path(text)] = None
    return list(found)


def check_out(parser: argparse.ArgumentParser, out, cases_root, names: Iterable[str], *, restricted: bool = True) -> Path:
    """Refuse a folder of --out that the records must not be written into, through `parser` and before the folder is made, and
    give the folder back as a resolved path. `cases_root` is the folder of --cases-root, or None for a subcommand without one;
    `names` are the files the subcommand writes, its manifest included.

    The paths are resolved first (os.path.realpath, and os.path.samefile where both are there), so that a link, a path with
    dots and another spelling of a name are seen for what they are. Refused, each with what to do: the cases root and what lies
    inside it; a folder inside a case folder (it or a folder above it holds a case.json); a cases root (a folder in it holds a
    case.json) and a data folder of the aid (a settings.json beside cases, po_store or inbox), and what lies inside one, also when
    no --cases-root says where the aid's folders are, with the name of the folder that is one; a path that is a file; and a folder
    that holds one of `names` already, or its partial file. With `restricted`, for records of restricted storage (the log, and the
    sheet of real cases), a folder inside a clone of a repository is refused too: it or a folder above it holds a .git, a folder
    in a clone and a file in a worktree. The command is run from the root of a clone, where a relative --out lands in it."""
    given, out = Path(out), Path(os.path.realpath(out))
    if cases_root is not None:
        root = Path(os.path.realpath(cases_root))
        if out == root or root in out.parents or any(_same(folder, root) for folder in (out, *out.parents)):
            parser.error(f"--out is the cases root (--cases-root) or lies inside it: {OUTSIDE}")
    if os.path.lexists(out) and not os.path.isdir(out):
        parser.error("--out is a file: give a folder")
    for folder in (out, *out.parents):
        if os.path.isfile(folder / CASE_FILE):       # its name holds a purchase order number: it is not shown
            parser.error(f"--out lies inside a case folder (a {CASE_FILE} stands in it or above it): {OUTSIDE}")
        if any(os.path.isfile(child / CASE_FILE) for child in _folders_in(folder)):
            parser.error(f"--out is a cases root or lies inside one ({folder.name or folder} holds a folder with a {CASE_FILE}): "
                         f"{OUTSIDE}")
        if os.path.isfile(folder / SETTINGS_FILE) and any(os.path.isdir(folder / name) for name in DATA_FOLDERS):
            parser.error(f"--out is a data folder of the aid or lies inside one ({folder.name or folder} holds a {SETTINGS_FILE} "
                         f"beside {', '.join(DATA_FOLDERS[:-1])} or {DATA_FOLDERS[-1]}): {OUTSIDE}")
        if restricted and os.path.lexists(folder / GIT):
            parser.error(f"--out lies inside a clone of a repository ({folder.name or folder} holds a {GIT}): the records are of "
                         "restricted storage: give a folder outside every clone")
    for name in names:
        for taken in (name, name + PARTIAL):
            if os.path.lexists(out / taken):
                parser.error(ALREADY.format(name=taken, folder=given))
    return out


def _same(one: Path, other: Path) -> bool:
    """Whether two paths are the same folder to the system, whatever their spelling; false when one of them is not there."""
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def _folders_in(folder: Path) -> list[Path]:
    """The folders directly in a folder; none for a folder that is not there or cannot be listed."""
    try:
        with os.scandir(folder) as entries:
            return [Path(entry.path) for entry in entries if entry.is_dir()]
    except OSError:
        return []


def write_all(out, files) -> list[Path]:
    """Write the files of a subcommand into the folder `out`, which is made when it is missing, and give their paths. `files`
    holds the content per file name: text, which is written as UTF-8 with the line ends it has, or bytes.

    Every file is first written under its partial name, and only when all are written are they renamed, one after the other. A
    failure on the way removes the partial files and the files that were renamed already, so that the folder is as it was. No
    file that is there is written over: a name that is taken, also by a partial file, is a Refusal before anything is written.
    So is a folder that cannot be made or written into, and a file that another program holds at the moment of its rename (a
    PermissionError), each with the sentence for the person at the keyboard."""
    out = Path(out)
    content = [(out / name, text.encode("utf-8") if isinstance(text, str) else bytes(text))
               for name, text in (files.items() if isinstance(files, Mapping) else files)]
    for path, _ in content:
        for taken in (path, _partial(path)):
            if os.path.lexists(taken):
                raise Refusal(ALREADY.format(name=taken.name, folder=out))
    made: list[Path] = []
    step = "the folder of --out cannot be made"
    try:
        out.mkdir(parents=True, exist_ok=True)
        step = "the records cannot be written into the folder of --out"
        for path, data in content:
            with open(_partial(path), "xb") as handle:
                made.append(_partial(path))
                handle.write(data)
        step = ""
        for path, _ in content:
            os.replace(_partial(path), path)
            made.append(path)
    except BaseException as error:
        for path in made:
            try:
                os.unlink(path)
            except OSError:
                pass                             # renamed already, or held: nothing more can be done about it here
        if isinstance(error, PermissionError) and not step:
            held = Path(error.filename).name.removesuffix(PARTIAL) if error.filename else "a file of the records"
            raise Refusal(HELD.format(name=held)) from None
        if isinstance(error, OSError):
            what = step or "the records cannot be renamed in the folder of --out"
            raise Refusal(f"{what} ({type(error).__name__}): give a folder you may write into") from None
        raise
    return [path for path, _ in content]


def _partial(path: Path) -> Path:
    """The name a file has while the files of a run are not all written."""
    return path.with_name(path.name + PARTIAL)


def write_manifest(out, name: str, *, inputs: Iterable = (), outputs=(), **facts) -> Path:
    """Write the manifest `name` of a subcommand into the folder `out` and give its path: what the records beside it were made
    from. It holds, in this order: the subcommand and its options (the keywords `subcommand` and `options`), the day, the aid's
    version, the base name and sha256 of every input and of every output file, and every other keyword as it is given (the
    number of cases, the set, the basis of the classes, the typed statements, the notes that apply).

    `inputs` are the input files: a path, which is read for its sha256, or a pair of a name and the bytes that were read, so
    that the sha256 is that of the bytes the records were made from. `outputs` is either the content per file name, as write_all
    takes it: those files are then written together with the manifest, as one set; or the names of files that write_all wrote
    into `out` already. A path among the options is reduced to its base name: no folder of the PC goes into a manifest. Keeping
    a purchase order number or the name of a case folder out of the options and the names is the work of the caller."""
    out = Path(out)
    files = dict(outputs) if isinstance(outputs, Mapping) else {}
    written = [(file, data.encode("utf-8") if isinstance(data, str) else bytes(data)) for file, data in files.items()]
    if not isinstance(outputs, Mapping):
        written = [(Path(file).name, timer_log.read_bytes(out / Path(file).name)) for file in outputs]
    read = [(entry[0], entry[1]) if isinstance(entry, tuple) else (Path(entry).name, timer_log.read_bytes(entry)) for entry in inputs]
    manifest = {"subcommand": facts.pop("subcommand", None), "options": _named(facts.pop("options", {})),
                "day": today().isoformat(), "aid_version": Deps.model_fields["aid_version"].default,
                "inputs": [_hashed(file, data) for file, data in read], "outputs": [_hashed(file, data) for file, data in written],
                **_named(facts)}
    write_all(out, files | {name: json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"})
    return out / name


def _hashed(name: str, data: bytes) -> dict[str, str]:
    """A file of a manifest: its base name and the sha256 of its bytes."""
    return {"name": Path(name).name, "sha256": hashlib.sha256(data).hexdigest()}


def _named(value):
    """A value of a manifest with every path in it reduced to its base name."""
    if isinstance(value, os.PathLike):
        return Path(value).name
    if isinstance(value, Mapping):
        return {key: _named(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_named(item) for item in value]
    return value


# --- the command line ---

def main(argv: list[str] | None = None) -> int:
    """Run the command (see the module docstring). Returns 0 when the records are written; a command line that is refused exits
    with 2. The subcommand has a parser of its own, which is made once the subcommand is known: raw_output.py, which adds the
    arguments of sheet and counts, is not needed for log."""
    top = argparse.ArgumentParser(prog=PROG, description="Write records of the evaluation from the timer's exports and from the aid's "
                                                         "case folders, which are only read.")
    top.add_argument("subcommand", choices=tuple(SUBCOMMANDS),
                     help="; ".join(f"{name}: {what}" for name, what in SUBCOMMANDS.items()))
    top.add_argument("arguments", nargs=argparse.REMAINDER,
                     help="the arguments of the subcommand; --help behind the subcommand lists them")
    chosen = top.parse_args(argv)
    parser = argparse.ArgumentParser(prog=f"{PROG} {chosen.subcommand}", description=f"Write {SUBCOMMANDS[chosen.subcommand]}.")
    try:
        if chosen.subcommand == "log":
            _add_log_arguments(parser)
            return _log(parser.parse_args(chosen.arguments), parser)
        raw_output = importlib.import_module("aid.evalscripts.raw_output")
        add, run = {"sheet": (raw_output.add_sheet_arguments, raw_output.run_sheet),
                    "counts": (raw_output.add_counts_arguments, raw_output.run_counts)}[chosen.subcommand]
        add(parser)
        _add_out(parser)
        return run(parser.parse_args(chosen.arguments), parser) or 0
    except (timer_log.Refusal, case_runs.Refusal) as refusal:
        parser.error(str(refusal))
    except OSError as error:                     # its text may hold the path of a case folder: only its class is shown
        parser.error(f"a file could not be read or written ({type(error).__name__}): run again")


def _add_out(parser: argparse.ArgumentParser) -> None:
    """Add --out, which every subcommand requires, unless the subcommand added it itself."""
    try:
        parser.add_argument("--out", type=Path, required=True, metavar="DIR",
                            help="the folder the records are written into: a new folder outside the aid's data folder")
    except argparse.ArgumentError:
        pass


# --- log: the per-case log and the check of the timer log ---

CHECK_FILE, LOG_FILE, LOG_MANIFEST = "timer_check.csv", "case_log.csv", "log_manifest.json"
CHECK_COLUMNS = ("case_id", "condition", "events", "log_complete", "start", "stop", "pauses", "pauses_closed", "waits", "waits_closed",
                 "consistent", "boundaries_unambiguous", "active_passive_apart", "hints", "problems")
LOG_COLUMNS = ("case_id", "month", "band", "analysis_band", "supplier_code", "condition", "session", "block", "position",
               "timer_events", "started", "stopped", "active_seconds", "passive_seconds", "aid_runs_in_handling",
               "aid_runs_outside_handling", "aid_call_attempts", "aid_run_seconds", "aid_cost_eur", "aid_reader", "aid_status",
               "aid_state", "closed_by", "version_selected", "aid_version", "flags_raw", "review_items", "could_not_compare",
               "dispositions", "judgement_issues", "manual_fallback", "fallback_reason", "completed", "deviations", "remarks")
# The columns behind the log when --seq is given.
SEQ_COLUMNS = ("seq_rating", "seq_recorded_at", "seq_status", "closed_by_rule", "seq_reason", "seq_note")
# The columns of the SEQ record (evaluation/templates/seq_ratings.csv); behind the case id, in the order of SEQ_COLUMNS.
SEQ_INPUT = ("case_id", "seq_rating", "recorded_at", "administration_status", "closed_by_rule", "reason", "note")
ASSIGNMENT_COLUMNS = ("order", "session", "block", "case_id", "band", "supplier_group", "condition")     # of evaluation/randomise.py
BANDS = ("1", "2 to 3", "4 or more")             # the bands of the assignment, by the number of lines (evaluation/randomise.py)
ALL_BANDS = "all"                                # the analysis band when the bands are combined (evaluation/analyse.py)
CONDITIONS = timer_log.CONDITIONS
SESSION = 8                                      # the cases of one session (evaluation/randomise.py)
NO_TIMING_GIVEN = "no timing export was given: give --timing, so that the seconds can be compared"
NO_EVENTS_GIVEN = "no events export was given: give --events, the timer log itself"
FROM_EVENTS = "seconds worked out from the events: no timing row agrees with them"
NO_RECORD = "no timer record: not handled yet, or its exports were not given"
NO_WAIT = "assisted case with a run of the aid and no wait event"
BY_RULE = "closed by rule: the timer's stop for such a case is still to settle"
NOT_LISTED = "not in the case list"
ASSISTED_NO_RUN = "assisted case: no run of the aid found"
ASSISTED_NONE_INSIDE = "assisted case: no run of the aid in the handling"
NONE_INSIDE = "no run of the aid in the handling"
NO_WINDOW = "no start and stop of the timer to place the runs of the aid in"
MANUAL_RUN = {"in": "run of the aid within the handling of a manual case", "before": "run before the handling",
              "after": "run after the handling", "unplaced": "run of the aid that cannot be placed against the handling"}
RUN_FAILED = "the aid's run failed"
OTHER_CONDITION = "the events carry the condition {events} where the assignment gives {planned}"
NOT_ASSIGNED = "not in the assignment"
UNPLACED = "run {n}: no audit record with a start time, so it cannot be placed against the handling"
STATE_UNREAD = "the present state of the case folder could not be read now ({error})"
ALIKE = "case ids that differ only in capitals, spaces or leading zeros"
NO_NUMBER = "a version_selected of the disposition log is not a whole number: not copied"
WITHHELD_FOR = "withheld for identifying text: {columns}"
SEQ_DISAGREES = "the SEQ record says closed_by_rule {said}; the case folder says {folder}"
UNCHECKED = "Typed text was copied without a check for identifying text: no case list was given."
UNCHECKED_FOR = "not checked for identifying text, as no case list was given: {columns}"
# What a reader of the log must know of the case folders it was made from: the notes of its manifest.
FOLDER_NOTES = ("attempts abandoned on a service failure leave no record in a case folder",
                "rows that a later close appended to a disposition log cannot be told from the rows written before the timer stopped")
NOT_FINISHED = f"handling is not finished: {LOG_FILE} is not written"
NEEDS_ASSIGNMENT = f"the events carry a session, so the timer ran with an assignment: give --assignment; {LOG_FILE} is not written"
CRITERION = "Criterion 1 of 3 (the timer log is complete): {n} of {m} cases"
RESEARCHERS = ("Criteria 2 and 3 (the boundaries of section 6.3 could be applied without ambiguity; active and passive time could be "
               "told apart) are the researcher's judgement: write yes or no in the columns boundaries_unambiguous and "
               "active_passive_apart of timer_check.csv.")


def _add_log_arguments(parser: argparse.ArgumentParser) -> None:
    """The arguments of log. A file argument may hold a star or a question mark and may be given more than once."""
    _add_out(parser)
    several = {"nargs": "+", "action": "extend", "default": [], "metavar": "FILE"}
    parser.add_argument("--events", **several, help="the events exports of the timer (timer_events_*.csv)")
    parser.add_argument("--timing", **several, help="the timing exports of the timer (timing_*.csv)")
    parser.add_argument("--assignment", metavar="FILE", help="the frozen assignment (the output of evaluation/randomise.py)")
    parser.add_argument("--case-list", metavar="FILE", help="the key between the case ids and the purchase orders; with --cases-root")
    parser.add_argument("--cases-root", metavar="DIR", type=Path, help="the cases folder of the aid's data folder; with --case-list")
    parser.add_argument("--dispositions", **several, help="disposition logs kept by hand (evaluation/templates/dispositions.csv)")
    parser.add_argument("--seq", metavar="FILE", help="the SEQ record (evaluation/templates/seq_ratings.csv)")
    parser.add_argument("--check-only", action="store_true", help=f"write {CHECK_FILE} only: for a run after each session")
    parser.add_argument("--while-running", action="store_true", help="read the case folders also while the aid runs on them")


@dataclass
class _Record:
    """One case of the log: its row of each of the two files, and what the summary on the terminal counts."""
    case_id: str
    check: dict[str, object]                     # the row of timer_check.csv
    log: dict[str, object]                       # the row of case_log.csv
    timed: bool                                  # the exports hold events or a timing row of the case
    complete: bool
    consistent: bool
    conditions: tuple[str, ...]                  # every condition the case was handled or planned in
    waited: bool
    fallback: bool
    by_rule: bool                                # the aid closed the case by its rules
    planned: bool                                # the case is in the assignment
    stopped: bool                                # its timer log ends with a stop that was not cancelled
    order: tuple                                 # where the row stands


@dataclass(frozen=True)
class _Planned:
    """One row of the assignment: what was planned for a case before any handling."""
    order: int
    session: str
    block: str
    case_id: str
    band: str
    supplier_group: str
    condition: str


@dataclass(frozen=True)
class _Kept:
    """A disposition log that was given: its rows, and the sha256 of its bytes, by which a copy of a file of a case folder is
    known."""
    sha256: str
    rows: list[dict[str, str]]


@dataclass
class _Given:
    """What the command line gave to log, as it was read."""
    events_given: bool
    timing_given: bool
    logs: dict[str, list[timer_log.Event]]       # the timer log of each case (timer_log.cases)
    timing: dict[str, list[timer_log.TimingRow]]                         # the rows of each case in the timing exports
    planned: dict[str, _Planned] | None = None   # the assignment by case id, in its order; None without one
    bands: dict[str, str] = field(default_factory=dict)                  # the analysis band of each band of the assignment
    listed: dict[str, case_runs.ListedCase] | None = None                # the case list by case id; None without one
    cases_root: Path | None = None
    found: case_runs.Folders | None = None       # the folders of the cases root, read once
    runs: dict[str, case_runs.CaseRuns] = field(default_factory=dict)    # the runs of each case of the case list
    dispositions: list[_Kept] | None = None      # the disposition logs given; None without one
    seq: dict[str, dict[str, str]] | None = None                         # the SEQ record by case id; None without one
    alike: dict[str, list[str]] = field(default_factory=dict)            # per case id, the ids that differ from it only in spelling
    withheld: int = 0                            # the cells of typed text that were withheld
    unchecked: int = 0                           # the cells of typed text that were copied without anything to check them against

    def holds_identifier(self, text: str) -> bool:
        """Whether a text holds identifying text (case_runs.holds_identifier): a purchase order number of the case list, the
        name of a case folder or of an input file of the cases root, or the supplier of a run that was read."""
        runs = [run for found in self.runs.values() for run in found.runs]
        return self.found is not None and case_runs.holds_identifier(text, self.listed.values(), [*self.found.read, *runs])

    def typed(self, text: str, column: str, withheld: list[str], unchecked: list[str] | None = None) -> str:
        """A text that the researcher typed, as it goes into the log: as it stands, or as WITHHELD of case_runs.py, with its
        column added to `withheld`, when it holds identifying text. Without a case list nothing is known to check it against: it
        is copied, and its column is added to `unchecked` (a code of the assignment is given without that list)."""
        if not text:
            return text
        if self.holds_identifier(text):
            withheld.append(column)
            self.withheld += 1
            return case_runs.WITHHELD
        if self.found is None and unchecked is not None:
            unchecked.append(column)
            self.unchecked += 1
        return text

    def named(self, value):
        """The base name of a file, or of each file of a list, for the manifest; withheld when it holds identifying text."""
        if isinstance(value, list):
            return [self.named(item) for item in value]
        return case_runs.WITHHELD if self.holds_identifier(Path(value).name) else Path(value).name


@dataclass
class _Aid:
    """What the case folders say about one case of the log."""
    cells: dict[str, object] = field(default_factory=dict)               # the columns of the log that the case folders fill
    remarks: list[str] = field(default_factory=list)
    runs: tuple[case_runs.Run, ...] = ()         # every run of the case
    inside: tuple[case_runs.Run, ...] = ()       # its runs in the handling
    the: case_runs.Run | None = None             # the run that counts among them
    why: str = ""                                # why a manual fallback followed: the run that counts failed or selected no version
    closed_by: str = ""                          # who closed the folder of the run that counts: "rule", "buyer", "replaced" or ""


def _log(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """The subcommand log: read what was given, make the two records and their manifest, and say what the check found."""
    paths = {name: expand(parser, getattr(args, name), f"--{name}") for name in ("events", "timing", "dispositions")}
    planned, listed, seq = (_one(parser, getattr(args, name), f"--{name.replace('_', '-')}")
                            for name in ("assignment", "case_list", "seq"))
    if not (paths["events"] or paths["timing"] or planned or listed):
        parser.error("log needs at least one of --events, --timing, --assignment and --case-list: give the timer's exports, the "
                     "assignment or the case list")
    if (listed is None) != (args.cases_root is None):
        parser.error("--case-list needs --cases-root: give the cases folder of the aid's data folder" if listed is not None
                     else "--cases-root needs --case-list: give the key between the case ids and the purchase orders")
    out = check_out(parser, args.out, args.cases_root, (CHECK_FILE, LOG_FILE, LOG_MANIFEST))
    if args.cases_root is not None and not os.path.isdir(args.cases_root):
        parser.error("--cases-root is not a folder that is there: give the cases folder of the aid's data folder")
    if args.cases_root is not None and not args.while_running and case_runs.aid_running(args.cases_root):
        parser.error(case_runs.AID_RUNS)
    checkable = args.cases_root is None or args.while_running or case_runs.aid_checkable(args.cases_root)
    read: list[tuple[str, bytes]] = []
    events = [event for path in paths["events"] for event in _parsed(timer_log.parse_events, path, "--events", read)]
    timing: dict[str, list[timer_log.TimingRow]] = {}
    for path in paths["timing"]:
        for row in _parsed(timer_log.parse_timing, path, "--timing", read):
            timing.setdefault(row.case_id, []).append(row)
    given = _Given(bool(paths["events"]), bool(paths["timing"]), timer_log.cases(events), timing)
    if planned is not None:
        given.planned = _parsed(_assignment, planned, "--assignment", read)
        cells = Counter((case.band, case.condition) for case in given.planned.values())
        given.bands = analysis_bands({band: {condition: cells[band, condition] for condition in CONDITIONS} for band in BANDS})
    if listed is not None:
        given.listed = {case.case_id: case for case in _case_list(listed, read)}
        given.cases_root, given.found = args.cases_root, case_runs.folders(args.cases_root)
        given.runs = {case_id: case_runs.runs_of(case, given.found) for case_id, case in given.listed.items()}
    if paths["dispositions"]:
        given.dispositions = [_parsed(_dispositions, path, "--dispositions", read) for path in paths["dispositions"]]
    if seq is not None:
        given.seq = _parsed(_seq, seq, "--seq", read)
    case_ids = dict.fromkeys([*(given.planned or ()), *given.logs, *given.timing, *(given.listed or ())])
    checks = {case_id: timer_log.check(given.logs.get(case_id, []), given.timing.get(case_id, [])) for case_id in case_ids}
    aids = {case_id: _aid(case_id, checks[case_id], given) for case_id in case_ids}
    closing = [aid for aid in aids.values() if aid.the is not None]
    try:
        states = case_runs.state_of(given.cases_root, [aid.the for aid in closing]) if closing else []
    except OSError as error:                     # a file that the aid is writing at this moment
        states = []
        for aid in closing:
            aid.remarks.append(STATE_UNREAD.format(error=type(error).__name__))
    for aid, state in zip(closing, states):
        aid.cells |= {"aid_state": state.state, "closed_by": state.closed_by}
        aid.closed_by = state.closed_by
    given.alike = _alike([*case_ids, *(given.seq or ())])
    records = sorted((_record(case_id, checks[case_id], aids[case_id], given) for case_id in case_ids),
                     key=lambda record: record.order)
    finished = all(record.stopped for record in records if record.planned)
    unplanned = given.planned is None and any(event.session for event in events)
    columns = LOG_COLUMNS + (SEQ_COLUMNS if given.seq is not None else ())
    files = {CHECK_FILE: csv_text(CHECK_COLUMNS, [record.check for record in records])}
    if finished and not unplanned and not args.check_only:
        files[LOG_FILE] = csv_text(columns, [record.log for record in records])
    files_given = (*paths.items(), ("assignment", planned), ("case_list", listed), ("seq", seq))
    options = {name: given.named(value) if value else value for name, value in files_given}
    options |= {"cases_root": args.cases_root, "check_only": args.check_only, "while_running": args.while_running}
    write_manifest(out, LOG_MANIFEST, subcommand="log", options=options, inputs=[(given.named(name), data) for name, data in read],
                   outputs=files, cases=len(records), notes=list(FOLDER_NOTES) if given.found is not None else [])
    print(_summary(records, columns if LOG_FILE in files else ()))
    if not checkable:
        print(case_runs.UNCHECKED_RUNNING)
    if given.found is not None and given.found.unreadable:
        passed = given.found.unreadable
        print(f"{_many(passed, 'case folder')} could not be read now and {'was' if passed == 1 else 'were'} passed over")
    for group in dict.fromkeys(tuple(sorted([case_id, *others])) for case_id, others in given.alike.items()):
        print(f"{ALIKE[0].upper()}{ALIKE[1:]}: {', '.join(group)}")
    unrated = [case_id for case_id in given.seq or () if case_id not in case_ids]
    if unrated:
        print(f"{_many(len(unrated), 'row')} of the SEQ record {'belongs' if len(unrated) == 1 else 'belong'} to no case of the log: "
              + ", ".join(unrated))
    if LOG_FILE in files and given.withheld:
        print(f"{_many(given.withheld, 'cell')} of typed text {'holds' if given.withheld == 1 else 'hold'} identifying text and "
              f"{'was' if given.withheld == 1 else 'were'} withheld")
    if LOG_FILE in files and given.unchecked:
        print(UNCHECKED)
    if not finished or unplanned:
        print(NEEDS_ASSIGNMENT if unplanned else NOT_FINISHED)
    print(f"Written into {args.out}: {', '.join([*files, LOG_MANIFEST])}")
    return 0


def analysis_bands(counts: Mapping[str, Mapping[str, int]]) -> dict[str, str]:
    """The analysis band of each band of the assignment, by the sparse-band rule of protocol section 9.2, decided from the
    allocation counts (the cases per band and condition) before any timing: a band with fewer than three cases in either
    condition is merged with the 2-to-3-line band; if both outer bands are sparse, or any band is still sparse after merging, the
    2-to-3-line band included, all bands are combined. It is sparse_merge of evaluation/analyse.py, written again here because the
    aid's own repository does not hold that script; a test compares the two where the script is present."""
    low, middle, high = BANDS
    sparse = {band: min(counts[band].values()) < 3 for band in BANDS}
    if sparse[low] and sparse[high]:
        return dict.fromkeys(BANDS, ALL_BANDS)
    merged = {band: middle if sparse[band] else band for band in BANDS}
    cells: dict[str, dict[str, int]] = {}
    for band in BANDS:
        for condition in CONDITIONS:
            cells.setdefault(merged[band], dict.fromkeys(CONDITIONS, 0))[condition] += counts[band][condition]
    return dict.fromkeys(BANDS, ALL_BANDS) if any(min(cell.values()) < 3 for cell in cells.values()) else merged


def _assignment(data: bytes, name: str) -> dict[str, _Planned]:
    """The cases of an assignment by case id, in its order. It is the output of the frozen script (evaluation/randomise.py): a
    file that lacks its columns, an order that is no whole number or stands twice, a case id that is missing or stands twice, and
    a condition or a band that the script does not write are refused with the row."""
    read = timer_log.table(data, name, ASSIGNMENT_COLUMNS, "an assignment")
    planned: dict[str, _Planned] = {}
    orders: set[int] = set()
    for number, row in zip(read.numbers, read.rows):
        problem = ("order is not a whole number from 1" if not re.fullmatch(r"[1-9][0-9]*", row["order"])
                   else f"the order {row['order']} stands twice" if int(row["order"]) in orders
                   else "no case id" if not row["case_id"]
                   else f"the case id {row['case_id']} stands twice" if row["case_id"] in planned
                   else "the condition is neither manual nor assisted" if row["condition"] not in CONDITIONS
                   else f"the band is not one of {', '.join(BANDS)}" if row["band"] not in BANDS else "")
        if problem:
            raise Refusal(f"{name}, row {number}: {problem}")
        orders.add(int(row["order"]))
        planned[row["case_id"]] = _Planned(int(row["order"]), **{column: row[column] for column in ASSIGNMENT_COLUMNS[1:]})
    return dict(sorted(planned.items(), key=lambda item: item[1].order))


def _dispositions(data: bytes, name: str) -> _Kept:
    """A disposition log that was given (the columns of evaluation/templates/dispositions.csv), with the sha256 of its bytes."""
    return _Kept(hashlib.sha256(data).hexdigest(), timer_log.table(data, name, DISPOSITION_COLUMNS, "a disposition log").rows)


def _seq(data: bytes, name: str) -> dict[str, dict[str, str]]:
    """The rows of a SEQ record by case id (the columns of evaluation/templates/seq_ratings.csv): one row per case, so that a row
    without a case id and a case id that stands twice are refused."""
    read = timer_log.table(data, name, SEQ_INPUT, "a SEQ record")
    rows: dict[str, dict[str, str]] = {}
    for number, row in zip(read.numbers, read.rows):
        if not row["case_id"] or row["case_id"] in rows:
            problem = f"the case id {row['case_id']} stands twice" if row["case_id"] else "no case id"
            raise Refusal(f"{name}, row {number}: {problem}")
        rows[row["case_id"]] = row
    return rows


def _alike(case_ids: Iterable[str]) -> dict[str, list[str]]:
    """Per case id, the other ids that differ from it only in capitals, spaces or leading zeros of a number: most likely one case
    that was typed in two ways, in the timer, the case list or the SEQ record. Nothing is merged on that ground."""
    groups: dict[str, list[str]] = {}
    for case_id in dict.fromkeys(case_ids):
        groups.setdefault(re.sub(r"(?<![0-9])0+(?=[0-9])", "", "".join(case_id.split()).casefold()), []).append(case_id)
    return {case_id: sorted(other for other in group if other != case_id)
            for group in groups.values() if len(group) > 1 for case_id in group}


def _parsed(parse, path: Path, argument: str, read: list[tuple[str, bytes]]):
    """What `parse` makes of the bytes of an input file; a Refusal of the file names the argument it was given for."""
    data = _read(path, argument, read)
    try:
        return parse(data, Path(path).name)
    except Refusal as refusal:
        raise Refusal(f"{argument}: {refusal}") from None


def _one(parser: argparse.ArgumentParser, pattern: str | None, argument: str) -> Path | None:
    """The one file of an argument that takes one file; a pattern that matches more than one is refused."""
    found = expand(parser, pattern, argument)
    if len(found) > 1:
        parser.error(f"{argument} takes one file: {len(found)} files match {Path(pattern).name}")
    return found[0] if found else None


def _read(path: Path, argument: str, read: list[tuple[str, bytes]]) -> bytes:
    """The bytes of an input file, read once and kept in `read` for the manifest. A file that is not there or that another program
    holds is a Refusal that names the argument."""
    try:
        data = timer_log.read_bytes(path)
    except Refusal as refusal:
        raise Refusal(f"{argument}: {refusal}") from None
    read.append((Path(path).name, data))
    return data


def _case_list(path: Path, read: list[tuple[str, bytes]]) -> list[case_runs.ListedCase]:
    """The cases of the case list (case_runs.read_case_list); its bytes are kept in `read` for the manifest."""
    try:
        cases = case_runs.read_case_list(path)
    except case_runs.Refusal as refusal:
        raise Refusal(f"--case-list: {refusal}") from None
    _read(path, "--case-list", read)
    return cases


def csv_text(columns: Iterable[str], rows: Iterable[Mapping[str, object]]) -> str:
    """A record as the text of a CSV file: the header and one line per row, with commas and \\n line ends on every system. A
    column that a row does not have is an empty cell."""
    columns = list(columns)
    written = io.StringIO(newline="")
    writer = csv.writer(written, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows([row.get(column, "") for column in columns] for row in rows)
    return written.getvalue()


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _many(count: int, one: str, more: str | None = None) -> str:
    """A count with its noun: "1 click", "2 clicks"."""
    return f"{count} {one if count == 1 else more or one + 's'}"


def _moment(timestamp: str) -> datetime:
    """A timestamp of the timer as a moment: the clock of the PC, written in UTC."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _record(case_id: str, found: timer_log.Check, aid: _Aid, given: _Given) -> _Record:
    """The rows of one case: the check of its timer log (`found`, of timer_log.check) as its row of timer_check.csv, and its row
    of the log, with what the case folders say about its runs (`aid`)."""
    log = given.logs.get(case_id, [])
    timed = bool(log or given.timing.get(case_id))
    planned = given.planned.get(case_id) if given.planned is not None else None
    condition = planned.condition if planned is not None else found.condition

    reworded = {timer_log.NO_TIMING_ROW: None if given.timing_given else NO_TIMING_GIVEN,
                timer_log.NO_EVENTS: None if given.events_given else NO_EVENTS_GIVEN}
    problems = [reworded.get(problem) or problem for problem in found.problems]
    other = [events for events in found.conditions if planned is not None and events and events != planned.condition]
    mine = ([OTHER_CONDITION.format(events=" and ".join(other), planned=planned.condition)] if other else []) + (
        [NOT_ASSIGNED] if given.planned is not None and planned is None and timed else [])
    consistent = found.consistent and not mine
    hints = list(found.notes)
    recoveries = sum(1 for event in found.effective if event.event == "recovery")
    undone = sum(1 for event in log if event.event == "undo")
    if recoveries:
        hints.append(f"{_many(recoveries, 'recovery choice')} in the log (a reload or a reopening): the gap was counted as the "
                     "researcher chose")
    if undone:
        hints.append(f"{_many(undone, 'click')} undone")
    if condition == "assisted" and aid.inside and not found.waits:
        hints.append(NO_WAIT)
    if aid.closed_by == "rule":
        hints.append(BY_RULE)
    check: dict[str, object] = {"case_id": case_id, "condition": condition, "events": found.events}
    if timed:
        check |= {"log_complete": _yes(found.log_complete), "start": _yes(found.start), "stop": _yes(found.stop),
                  "pauses": found.pauses, "pauses_closed": found.pauses_closed, "waits": found.waits,
                  "waits_closed": found.waits_closed, "consistent": _yes(consistent), "problems": "; ".join(problems + mine)}
    else:
        hints.append(NO_RECORD)
    check["hints"] = "; ".join(hints)

    remarks = list(aid.remarks) + ([f"{ALIKE}: {', '.join(given.alike[case_id])}"] if case_id in given.alike else [])
    decided, versions = _decided(case_id, aid, condition, given, remarks)
    withheld: list[str] = []
    unchecked: list[str] = []
    deviations = found.timing.deviations if found.timing is not None else "; ".join(event.note for event in log if event.note)
    rated = given.seq.get(case_id, {}) if given.seq is not None else {}
    seq = {column: rated.get(source, "") for column, source in zip(SEQ_COLUMNS, SEQ_INPUT[1:])}
    said = seq["closed_by_rule"].casefold()
    if said in ("yes", "no") and aid.the is not None and (said == "yes") != (aid.closed_by == "rule"):
        remarks.append(SEQ_DISAGREES.format(said=said, folder=f"closed by {aid.closed_by}" if aid.closed_by else "not closed"))
    active = passive = ""
    if found.timing is not None:
        active, passive = str(found.timing.active_seconds), str(found.timing.passive_seconds)
    elif found.stop:
        active, passive = timer_log.tenth(found.seconds.active), timer_log.tenth(found.seconds.passive)
        remarks.append(FROM_EVENTS)
    listed = given.listed.get(case_id) if given.listed is not None else None
    row = {"case_id": case_id, "month": listed.month if listed is not None else "", "condition": condition,
           "session": planned.session if planned is not None else log[0].session if log else "",
           **({"band": planned.band, "analysis_band": given.bands[planned.band], "block": planned.block,
               "supplier_code": given.typed(planned.supplier_group, "supplier_code", withheld),
               "position": 1 + (planned.order - 1) % SESSION} if planned is not None else {}),
           "timer_events": "; ".join(f"{event.event} {event.timestamp}" for event in found.effective),
           "started": found.started, "stopped": found.stopped, "active_seconds": active, "passive_seconds": passive,
           **aid.cells, "version_selected": versions, "dispositions": decided,
           "manual_fallback": _yes(found.fallback) if log else "", "fallback_reason": aid.why if found.fallback else "",
           "completed": {"complete": "yes", "incomplete": "no"}.get(found.status, ""),
           "deviations": given.typed(deviations, "deviations", withheld, unchecked),
           **seq, **{column: given.typed(seq[column], column, withheld, unchecked) for column in ("seq_reason", "seq_note")}}
    if withheld:
        remarks.append(WITHHELD_FOR.format(columns=", ".join(withheld)))
    if unchecked:
        remarks.append(UNCHECKED_FOR.format(columns=", ".join(unchecked)))
    row["remarks"] = "; ".join(dict.fromkeys(remarks))
    started = timer_log.clock_ms(found.started) if found.started else None
    conditions = tuple(dict.fromkeys([*([planned.condition] if planned is not None else []), *found.conditions]))
    return _Record(case_id, check, row, timed, found.log_complete, consistent, conditions, found.waits > 0, found.fallback,
                   aid.closed_by == "rule", planned is not None, found.stop,
                   (0, planned.order) if planned is not None else (1, started is None, started or 0, case_id))


def _aid(case_id: str, check: timer_log.Check, given: _Given) -> _Aid:
    """What the case folders say about one case (see the module docstring): nothing without a case list; for a manual case only
    a remark on the runs found; else the runs in the handling, their figures, and the output of the run that counts among them.
    The present state of that run's folder is added by the caller, for all cases at once."""
    if given.found is None:
        return _Aid()
    if case_id not in given.listed:
        return _Aid(remarks=[NOT_LISTED])
    case = given.listed[case_id]
    condition = given.planned[case_id].condition if case_id in (given.planned or ()) else check.condition
    window = (_moment(check.started), _moment(check.stopped)) if check.started and check.stopped else None
    found = given.runs[case.case_id]
    runs = found.runs
    remarks = [f"run {number}: {name} could not be read now ({error})"
               for number, run in enumerate(runs, start=1) for name, error in run.unread]
    figures = [case_runs.figures_of(run) for run in runs]
    places = [_place(figure.started_at, window) for figure in figures]
    if condition == "manual":
        return _Aid(remarks=remarks + [MANUAL_RUN[place] for place in MANUAL_RUN if place in places], runs=runs)
    if not runs:
        no_run = ASSISTED_NO_RUN if condition == "assisted" and found.reason == case_runs.NO_RUN else found.reason
        return _Aid(remarks=remarks + [no_run])
    inside = [index for index, place in enumerate(places) if place == "in"]
    unplaced = [number for number, place in enumerate(places, start=1) if place == "unplaced"]
    if window is not None:
        remarks += [UNPLACED.format(n=number) for number in unplaced]
    outside = sum(1 for place in places if place in ("before", "after"))
    counts = {"aid_runs_in_handling": len(inside), "aid_runs_outside_handling": outside} if window is not None else {}
    aid = _Aid(counts, remarks, runs, tuple(runs[index] for index in inside))
    if not inside:
        if window is None:
            aid.remarks.append(NO_WINDOW)
        elif not unplaced:
            aid.remarks.append(ASSISTED_NONE_INSIDE if condition == "assisted" else NONE_INSIDE)
        return aid
    mine = [figures[index] for index in inside]
    aid.remarks += [remark for figure in mine for remark in figure.remarks]
    attempts, seconds, costs = ([getattr(figure, name) for figure in mine] for name in ("call_attempts", "seconds", "cost_eur"))
    taken = sum((Decimal(str(run_seconds)) for run_seconds in seconds if run_seconds is not None), Decimal(0))
    aid.cells |= {"aid_call_attempts": sum(attempts) if None not in attempts else "",
                  "aid_run_seconds": timer_log.tenth(taken) if None not in seconds else "",
                  "aid_cost_eur": str(sum(costs, Decimal(0))) if None not in costs else "",
                  "aid_reader": "; ".join(dict.fromkeys(figure.reader for figure in mine if figure.reader))}
    the = case_runs.counted(aid.inside)
    if the.note:
        aid.remarks.append(the.note)
    aid.the = the.run
    if the.run is None:
        return aid
    if len(inside) > 1:
        aid.remarks.append(f"{the.label} in the handling counts")
    output = case_runs.output_of(the.run)
    if not output.kept:
        aid.remarks.append(output.reason)
    else:
        choice, findings, result = output.choice, output.findings, output.case_result
        version = f"{'none' if choice.version is None else choice.version} ({choice.method})" if choice is not None else ""
        aid.cells |= {"aid_status": output.status or "", "aid_version": version,
                      "flags_raw": len(findings.flags) if findings is not None else "",
                      "could_not_compare": len(findings.uncompared) if findings is not None else "",
                      "review_items": len(result.review_items) if result is not None else ""}
        aid.why = RUN_FAILED if output.status == "failed" else NO_VERSION if output.lacks == NO_VERSION else ""
    return aid


def _decided(case_id: str, aid: _Aid, condition: str, given: _Given, remarks: list[str]) -> tuple[object, str]:
    """The number of dispositions of a case and the version its disposition log names: the data rows of the dispositions.csv of
    the folders of its runs in the handling, whatever their case_id column holds (the aid writes the name of the folder there),
    and the rows of the disposition logs given that carry the case id or the name of a folder of the case. A log given that is a
    copy of a file of those folders is not counted again. For a manual case only the rows with its case id count: the aid's own
    log of a replay is no part of the handling. The number is blank when no disposition log of the case is known. No cell of a
    row goes into the log but the version, and that only as a whole number."""
    rows: list[dict[str, str]] = []
    own: set[str] = set()
    for run in aid.inside:
        number = aid.runs.index(run) + 1
        try:
            data = (run.folder.path / DISPOSITION_FILE).read_bytes()
            rows += timer_log.table(data, DISPOSITION_FILE, ("version_selected",)).rows
            own.add(hashlib.sha256(data).hexdigest())
        except (FileNotFoundError, NotADirectoryError):
            continue                             # the run wrote no disposition
        except OSError as error:
            remarks.append(f"run {number}: {DISPOSITION_FILE} could not be read now ({type(error).__name__})")
        except Refusal:
            remarks.append(f"run {number}: {DISPOSITION_FILE} is not a disposition log that can be read")
    names = {case_id} | ({run.folder.name for run in aid.runs} if condition != "manual" else set())
    for kept in given.dispositions or ():
        rows += [row for row in kept.rows if row["case_id"] in names] if kept.sha256 not in own else []
    versions = list(dict.fromkeys(row["version_selected"] for row in rows if row["version_selected"]))
    numbers = [version for version in versions if re.fullmatch(r"[0-9]+", version)]
    if len(numbers) < len(versions):
        remarks.append(NO_NUMBER)
    return len(rows) if aid.inside or given.dispositions is not None else "", "; ".join(numbers)


def _place(started: datetime | None, window: tuple[datetime, datetime] | None) -> str:
    """Where the start of a run lies against the handling of its case, from the first start of the timer to its stop: "in",
    "before" or "after"; "unplaced" for a run without a start and for a case without a start and a stop."""
    if started is None or window is None:
        return "unplaced"
    return "before" if started < window[0] else "after" if started > window[1] else "in"


def _summary(records: list[_Record], columns: Iterable[str]) -> str:
    """What the terminal says after log: the verdict on criterion 1, the cases that are not complete or not consistent with their
    problems, what the cases held, how many log items stayed blank, and whose the other two criteria are. No time and no seconds."""
    timed = [record for record in records if record.timed]
    lines = [CRITERION.format(n=sum(1 for record in timed if record.complete), m=len(timed))]
    wrong = [record for record in timed if not (record.complete and record.consistent)]
    if wrong:
        lines.append("Not complete or not consistent:")
        for record in wrong:
            verdict = ("complete" if record.complete else "not complete", "consistent" if record.consistent else "not consistent")
            lines.append(f"  {record.case_id} ({', '.join(verdict)}): {record.check['problems']}")
    per = [f"{sum(1 for record in records if condition in record.conditions)} {condition}" for condition in timer_log.CONDITIONS]
    counts = {"in both conditions": sum(1 for record in records if len(set(record.conditions)) > 1),
              "with a wait": sum(1 for record in records if record.waited),
              "with a fallback": sum(1 for record in records if record.fallback),
              "closed by rule": sum(1 for record in records if record.by_rule),
              "without a timer record": len(records) - len(timed)}
    lines.append(f"Cases: {', '.join(per)}; " + "; ".join(f"{what} {count}" for what, count in counts.items()))
    blank = {column: sum(1 for record in records if record.log.get(column, "") == "") for column in columns}   # no column: no log
    if any(blank.values()):
        lines.append("Log items left blank, by the number of cases: "
                     + ", ".join(f"{column} {count}" for column, count in blank.items() if count))
    lines.append(RESEARCHERS)
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
