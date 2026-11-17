"""The sheet of the aid's unreviewed output and the counts made from it: the subcommands sheet and counts of the records command.

    python -m aid.evalscripts.records sheet  --out DIR --set SET --case-list FILE --cases-root DIR [--kind confirmation|cart]
                                             [--references FILE [--references-locked YYYY-MM-DD]] [--allow-not-run] [--while-running]
    python -m aid.evalscripts.records counts --out DIR --set SET --classes mechanical|rater --sheet FILE

The evaluation scores what the aid said before anyone reviewed it, item by item, against references built from the documents
(protocol section 8.2; scoring manual, sections 4 and 8). The record of that is a sheet, raw_output_scores, with one row per
item, and the totals are derived from its rows and from nothing else. sheet writes that sheet filled in as far as a lookup can
fill it, from the audit record of each case's run; counts reads a sheet, as it was written or as a rater completed it, and
writes the counts per field. SET is made-up, development, held-out or challenge, and one sheet holds one control point (--kind):
the confirmations and the carts are never pooled (protocol section 2).

THE SHEET (sheet_rows for one case; reading_of for what it takes of a case folder)

The header is that of evaluation/templates/raw_output_scores.csv. A case has first its version row (no field), then, with
references, for each reference item one row per item of the aid on it, or one row that says the aid has none; then one row for
each item of the aid that belongs to no reference item. Without references it has one row per item of the aid, with the
reference columns and the class empty. The items of the aid are those of the run that counts (aid/evalscripts/case_runs.py): its
flags (F1, ...), its could-not-compare items (U1, ...) and its matches.

The references (read_references) have the header of evaluation/templates/references.csv. Their labels are compared without
capitals and outer spaces and are accepted in the words of the made-up references and of the scoring manual: same (present for
required information), different, missing on the confirmation (missing for required information), cannot be settled (from the
documents), unresolved, not applicable; the line labels matched, split, merged, missing, extra, unresolved; the version a whole
number or unresolved. Any other word refuses the file, naming the word and the reference item id: a label without a rule would
get no class, or a wrong one, without a word.

The join. An item of the aid sits on a reference item of the same field: one with the line label extra is keyed by the line of
the document, every other by the PO line, and required information by the case. One item of the aid can sit on several reference
items (a merged line). A reference line that the run's own line pairs do not hold is written as "line id not found in the aid's
reading", without a class: an id in another form must not read as no output, and so as a miss. The field items of a line that is
wholly missing or extra are written as "not counted: the line is missing or extra" (scoring manual, section 4).

The columns.
- line_pair_id: "P1+2|D1", "P4|D-" (no line of the document answers), "P-|D3" (answers no PO line): a form that no spreadsheet
  reads as a time.
- reference_label: the label in the words above; for a line presence with the line label behind it, "same (merged)", so that the
  counts can tell the lines by label from the sheet alone; for the version row "version n" or "unresolved".
- note: begins with what the aid asserts, in fixed words ("aid: different", "aid: could not compare (total only)", "aid: same",
  "aid: no output for this item", "aid: no output for this case (failed)", "aid: not run"). The counts read that beginning and
  the fixed markers behind it, so a rater writes a remark behind them.
- detection_class. A row whose class follows from the label is filled: a match on a same item "no difference"; a flag on a same
  item "false flag"; a match, or no output, on a comparable difference "miss"; a could-not-compare item on a comparable item
  "abstention"; on an item the documents do not settle a could-not-compare item is "justified", a flag "outside the detection
  counts" and a match "match on unsettled item"; no output on an item that is no difference "no output"; an item that does not
  apply "not scored"; an item of the aid without a reference item "cannot be mapped". A flag on a comparable difference is a
  judgement (scoring manual, section 4, first row): its class stays empty and the note holds "proposal: true flag" or
  "proposal: false flag (the defect)", and what is left to judge. The proposal is a true flag when the detail of the flag agrees
  with the label, no source fails, and the flag cites the line of the document that the reference cites, where both name one.
- duplicate_of: a flag with the field, lines, detail, values and sources of an earlier flag of the case is its copy: it names
  that flag and has no class of its own.
- source_traceable, for a flag. Each side is judged on its own: a side without a text layer to check (a saved page, an image, a
  scan, a PO value from an export, a file that is gone or has changed since the run, a value entered in Exact) is not checked;
  on a side with one, the value must cite a line that holds its text (the rule of aid/evalscripts/score_reading.py on the lines
  of aid/readers/pdf_text.py). The flag is "no" when a side fails, "yes" when every side that exists was checked and cites, and
  empty otherwise; the note says which. The values of matches are not checked: the column says nothing about them.
- comparable: "no" for cannot be settled and unresolved, empty for not applicable, "yes" otherwise.
- version_status, on the version row: right, version-selection error, abstention on the version (the aid's "cannot select"), no
  stored version, reference unresolved, no output (the run failed), not run, first output not kept. The note names the version
  and a fixed word for the method, never the reason, which holds a date of the document or the text of a model. With a wrong
  version the classes that depend on the lines are left to the rater, unless both versions hold the same line ids.
- line_match_status, on a line-presence row: right (the kind of pair agrees with the line label; which line of the document was
  paired is not checked), forced match (a pair on a line the reference labels missing or unresolved), missed split or merge,
  uncertain (the aid left the line uncertain), other.
A case that was not run, and a case whose first output is not kept, have the version row only.

Seven of these words are the documents' own (true flag, false flag, miss, justified, abstention, version-selection error,
abstention on the version). The others are this tool's and are to be read as defined here.

Nothing but ids and fixed words is written: no value of a document, no purchase order number, no name of a folder or a file, no
supplier and no reason that the aid or a model wrote. The ids of the cases and of the reference items are written as the case
list and the references give them. The case folders, the stored PO files and the inputs are only read, each file with one read
of its bytes. The sheet of real cases is a record of restricted storage; only counts may go to the repository.

The order of work (protocol section 7.1). For held-out cases the references and --references-locked, the day they were locked,
are required: the aid's output stays closed while the references are built. The statement is kept in the manifest. A case of the
case list without a run is refused by its id, unless --allow-not-run: not run is not no output, whose differences are misses.

THE COUNTS (read_sheet, count)

A sheet is read strictly, from .csv or .xlsx (raw_output_scores.xlsx is the file for the rater: every cell text, a list to
choose from in each closed column). The cells are stripped, their inner spaces folded and their capitals ignored. Refused, with
the row of the file, the column and what it may hold: a value outside the list of its column, a truth value or an error code
that a spreadsheet made of a cell, a line pair or a flag id that does not read as one, a row that stands twice, a case without
its version row, and a reference item whose rows disagree. A typing error would otherwise change the counts without a word.

The basis (--classes) is written into every row of every file, with the set. Under rater the class of a row is the cell: a row
that needs a class and has none is unscored, the unscored rows are counted, and no recall and no precision is written while one
is left. Under mechanical an empty class takes the proposal of the note: this is what the made-up set is counted by, and it is
no rater's score. For the challenge set no recall and no precision is written (protocol section 8.6). A ratio is written as
"12 of 13", which no spreadsheet reads as another number, and as "undefined" over nothing. No interval is computed.

raw_output_counts.csv has a row per field and a row total, computed from the sheet itself. Reference items are counted by their
id, flags and could-not-compare items by their case and id. Every comparable reference difference counts once, as a detection
(one of its rows is a true flag) or as a miss (misses_by_abstention are the misses with a could-not-compare item); every
distinct flag counts once, as true or as false, a false flag without a reference item included, and its copies are counted as
duplicate_flags only. compared_items are the comparable items with a flag or a match, and coverage is their share. Six fields
are counted where the protocol names five: it has quantity and unit as one field, which the references and the aid keep apart.
raw_output_cases.csv has a row per case of the sheet, the cases with zero counts too. raw_output_totals.csv gives the figures
per case with their denominators: the cases with output, without output and not run; the version-selection errors and
abstentions over the cases with a settled version reference; the line-matching errors over the PO lines by line label; the
false flags per case (protocol section 8.5); the duplicate flags; and the detection figures of the cases with a
version-selection error (scoring manual, section 4). A case that was not run is in the number of cases and outside every
denominator. The counts read the sheet alone: the counting code imports nothing of the module that reads the case folders.
"""
import argparse
import csv
import hashlib
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import ValidationError

from aid.comparison import Findings, Flag, Match, Uncompared
from aid.evalscripts.score_reading import _cites, _collapsed
from aid.evalscripts.timer_log import Refusal, Table, read_bytes, table
from aid.matching import MatchResult
from aid.po_store import PoVersion
from aid.readers.pdf_text import extract_lines
from aid.routing import findings_by_id
from aid.schema import DocValue
from aid.versioning import VersionChoice

# The header of the sheet: that of evaluation/templates/raw_output_scores.csv.
SHEET_COLUMNS = ("case_id", "reference_item_id", "line_pair_id", "field", "reference_label", "flag_id", "detection_class",
                 "duplicate_of", "source_traceable", "comparable", "version_status", "line_match_status", "note")

FIELDS = ("price", "quantity", "unit", "delivery_date", "line_presence", "required_info")
PAIR_FIELDS = FIELDS[:4]                         # the fields of a line pair
# The labels of a reference item, in the words the sheet writes, and the other words a references file may use for them: the
# scoring manual says present and missing for required information, the made-up references same and missing on the confirmation.
SAME, DIFFERENT, MISSING = "same", "different", "missing on the confirmation"
UNSETTLED, UNRESOLVED, NOT_APPLICABLE = "cannot be settled", "unresolved", "not applicable"
LABELS = {SAME: SAME, DIFFERENT: DIFFERENT, MISSING: MISSING, UNSETTLED: UNSETTLED, "cannot be settled from the documents": UNSETTLED,
          UNRESOLVED: UNRESOLVED, NOT_APPLICABLE: NOT_APPLICABLE}
REQUIRED_LABELS = LABELS | {"present": SAME, "missing": MISSING}
DIFFERENCES = (DIFFERENT, MISSING)               # the labels of a reference difference
NOT_COMPARABLE = (UNSETTLED, UNRESOLVED)         # the labels of an item the documents do not settle
# The item types of a references file, each with the field its items have ("" where the row names it), and the line labels with
# the labels of a line-presence item that go with each (scoring manual, section 2).
VERSION_ITEM, PRESENCE_ITEM, FIELD_ITEM, REQUIRED_ITEM = "version", "line presence", "field", "required information"
ITEM_FIELDS = {VERSION_ITEM: "", PRESENCE_ITEM: "line_presence", FIELD_ITEM: None, REQUIRED_ITEM: "required_info"}
EXTRA = "extra"
LINE_LABELS = {"matched": (SAME,), "split": (SAME,), "merged": (SAME,), "missing": (MISSING,), EXTRA: (DIFFERENT,),
               UNRESOLVED: NOT_COMPARABLE}
REFERENCE_COLUMNS = ("case_id", "reference_item_id", "item_type", "version_answered", "line_id", "line_label", "field", "label",
                     "source")
_CITED = re.compile(r"\bpage\s+(\d+)\s*,\s*line\s+(\d+)", re.IGNORECASE)


# --- the references ---

@dataclass(frozen=True)
class ReferenceItem:
    """One item of a references file, in the words the sheet writes."""
    case_id: str
    item_id: str
    item_type: str                               # "version", "line presence", "field" or "required information"
    field: str                                   # one of FIELDS; "" for the version item
    line_id: str                                 # the PO line, the document line for an extra line; "" for the case as a whole
    line_label: str                              # matched, split, merged, missing, extra or unresolved; "" where the file has none
    label: str                                   # same, different, missing on the confirmation, cannot be settled, unresolved or
    #                                              not applicable; "" for the version item
    version: int | None = None                   # the version item: the version the document answers, None for unresolved
    cites: tuple[int, int] | None = None         # the page and the line of the document that the source names, when it names one

    @property
    def difference(self) -> bool:
        """Whether the item is a reference difference: different, or missing on the confirmation."""
        return self.label in DIFFERENCES

    @property
    def comparable(self) -> str:
        """The column comparable of the sheet: "no" for an item the documents do not settle, "" for one that does not apply and
        for the version item, "yes" otherwise."""
        if self.label in NOT_COMPARABLE:
            return "no"
        return "" if self.label in ("", NOT_APPLICABLE) else "yes"


def read_references(data: bytes, name: str) -> dict[str, list[ReferenceItem]]:
    """The reference items of a references file (the header of evaluation/templates/references.csv), by case, in the order of the
    file. The words are compared without capitals and the spaces around them. Raises Refusal for a file without its columns and
    for a word outside the lists: a label, a line label, an item type, a field or an answered version that the sheet has no rule
    for, a line-presence label that does not go with its line label, a case without exactly one version item, and an item id that
    stands twice. The sentence names the row or the reference item id and the word, and nothing that a document holds."""
    read = table(data, name, REFERENCE_COLUMNS, "a references file")
    cases: dict[str, list[ReferenceItem]] = {}
    seen: dict[str, int] = {}
    for number, row in zip(read.numbers, read.rows):
        case_id, item_id = row["case_id"], row["reference_item_id"]
        for column, what in ((case_id, "case id"), (item_id, "reference item id")):
            if not column:
                raise Refusal(f"{name}: row {number} has no {what}")
        if item_id in seen:
            raise Refusal(f"{name}: the reference item id {item_id} stands twice (rows {seen[item_id]} and {number})")
        seen[item_id] = number
        cases.setdefault(case_id, []).append(_item(name, row))
    for case_id, items in cases.items():
        versions = sum(item.item_type == VERSION_ITEM for item in items)
        if versions != 1:
            raise Refusal(f"{name}: case {case_id} has {versions} version items: a case has one")
    return cases


def _item(name: str, row: dict[str, str]) -> ReferenceItem:
    """The reference item of one row of a references file; Refusal for a word outside the lists."""
    item_id = row["reference_item_id"]
    words = {column: " ".join(row[column].lower().split())
             for column in ("item_type", "line_label", "field", "label", "version_answered")}

    def refuse(what: str, word: str, allowed) -> Refusal:
        return Refusal(f"{name}: {item_id} has the {what} {word!r}: it is one of {', '.join(sorted(allowed))}")

    item_type, line_label, label = words["item_type"], words["line_label"], words["label"]
    if item_type not in ITEM_FIELDS:
        raise refuse("item type", row["item_type"], ITEM_FIELDS)
    cited = _CITED.search(row["source"])
    cites = (int(cited[1]), int(cited[2])) if cited else None
    if item_type == VERSION_ITEM:
        answered = words["version_answered"]
        if not (answered.isdecimal() or answered == UNRESOLVED):
            raise Refusal(f"{name}: {item_id} has the answered version {row['version_answered']!r}: it is a whole number or "
                          f"{UNRESOLVED}")
        if label not in ("", UNRESOLVED):
            raise Refusal(f"{name}: {item_id} has the label {row['label']!r}: a version item has none, its answer stands in "
                          "version_answered")
        version = int(answered) if answered.isdecimal() else None
        return ReferenceItem(row["case_id"], item_id, item_type, "", "", "", "", version, cites)
    of_field = ITEM_FIELDS[item_type] or words["field"]
    if of_field not in PAIR_FIELDS and item_type == FIELD_ITEM:
        raise refuse("field", row["field"], PAIR_FIELDS)
    accepted = REQUIRED_LABELS if item_type == REQUIRED_ITEM else LABELS
    if label not in accepted:
        raise refuse("label", row["label"], accepted)
    if line_label not in LINE_LABELS and (line_label or item_type == PRESENCE_ITEM):
        raise refuse("line label", row["line_label"], LINE_LABELS)
    if item_type == PRESENCE_ITEM and accepted[label] not in LINE_LABELS[line_label]:
        raise Refusal(f"{name}: {item_id} has the label {row['label']!r} with the line label {row['line_label']!r}: a line that is "
                      f"{line_label} has the label {' or '.join(LINE_LABELS[line_label])}")
    line_id = "" if item_type == REQUIRED_ITEM else row["line_id"]
    return ReferenceItem(row["case_id"], item_id, item_type, of_field, line_id, line_label, accepted[label], None, cites)


# --- the output of the run that counts ---

# What stands in the way of an output of a case (Reading.lacks). The last three are the words of aid/evalscripts/case_runs.py.
NOT_RUN, NO_OUTPUT, NO_VERSION, NOT_KEPT = "not run", "no output", "no version selected", "first output not kept"
# The classes of the column detection_class. The first two are the rater's to give on a flag on a comparable difference.
TRUE_FLAG, FALSE_FLAG, MISS, NO_DIFFERENCE = "true flag", "false flag", "miss", "no difference"
JUSTIFIED, ABSTENTION, OUTSIDE, UNSETTLED_MATCH = "justified", "abstention", "outside the detection counts", "match on unsettled item"
NOT_SCORED, UNMAPPED = "not scored", "cannot be mapped"
CLASSES = (TRUE_FLAG, FALSE_FLAG, MISS, NO_DIFFERENCE, JUSTIFIED, ABSTENTION, OUTSIDE, UNSETTLED_MATCH, NO_OUTPUT, NOT_SCORED, UNMAPPED)
# The values of the column version_status and of the column line_match_status.
RIGHT, VERSION_ERROR, VERSION_ABSTENTION = "right", "version-selection error", "abstention on the version"
REFERENCE_UNRESOLVED, NO_STORED_VERSION, CANNOT_SELECT = "reference unresolved", "no stored version", "cannot select"
VERSION_STATUSES = (RIGHT, VERSION_ERROR, VERSION_ABSTENTION, REFERENCE_UNRESOLVED, NO_OUTPUT, NO_STORED_VERSION, NOT_RUN, NOT_KEPT)
FORCED, MISSED_SPLIT, UNCERTAIN, OTHER = "forced match", "missed split or merge", "uncertain", "other"
LINE_MATCH_STATUSES = (RIGHT, FORCED, MISSED_SPLIT, UNCERTAIN, OTHER)
# The fixed words of a note. A note begins with what the aid asserts; the counts read these beginnings and markers.
AID = "aid: "
SAYS_SAME = AID + "same"
NO_ITEM_OUTPUT = AID + "no output for this item"
NO_CASE_OUTPUT = AID + "no output for this case ({why})"
NOT_LOOKED_UP = AID + "not looked up"
PROPOSAL = "proposal: "
NOT_COUNTED = "not counted: the line is missing or extra"
NOT_FOUND = "line id not found in the aid's reading"
WRONG_VERSION = "wrong version: class left to the rater"
ENTERED = "aid compared with a value entered in Exact"
ENTERED_UNREAD = "the values entered in Exact could not be read at the run"
UNREAD_NOW = "{n} file{s} of the run could not be read now: run the command again"
NO_TRACE = "a value of the flag has no traceable source"
OTHER_LINE = "the flag cites another line of the document than the reference"
# The details of a flag (aid/comparison.py) that agree with each label of a reference difference.
AGREES = {DIFFERENT: ("different", "later", "earlier", "extra line"),
          MISSING: ("missing on the document", "missing line", "not in cart")}
# Where each kind of place the aid gave a line agrees with a line label: a pair of its kind, no line that answers, no PO line.
UNMATCHED = "unmatched"
PLACE_OF = {"matched": "matched", "split": "split", "merged": "merged", "missing": UNMATCHED, EXTRA: EXTRA}
PAIR_KINDS = ("matched", "split", "merged")


@dataclass(frozen=True)
class Text:
    """The text layer of the file that one side of a case was read from, as far as a source can be checked against it."""
    lines: Mapping[tuple[int, int], str] | None = None     # the text of each line by its page and number; None: nothing to check
    why: str = "not looked up"                              # why there is nothing to check: "saved page", "export", ...


@dataclass(frozen=True)
class Reading:
    """What the sheet takes of a case: the unreviewed output of its run that counts, and what a source is checked against."""
    lacks: str = ""                              # NOT_RUN, NOT_KEPT, NO_OUTPUT or NO_VERSION; "" for a run that compared the document
    choice: VersionChoice | None = None          # the version the aid selected; None when the run did not get that far
    pairs: MatchResult | None = None             # the line pairs
    findings: Findings | None = None             # the flags, the matches and the could-not-compare items
    entered: frozenset[tuple[str, str]] = frozenset()      # the PO line and field of each value entered in Exact that was compared
    version_lines: Mapping[int, frozenset[str]] = field(default_factory=dict)     # the line ids of each stored version of the PO
    document: Text = Text()                      # the text layer of the supplier's document
    po: Text = Text()                            # the text layer of the selected PO version
    run: str = ""                                # which run counts: "run 2 of 2"
    remarks: tuple[str, ...] = ()                # fixed sentences for the note of the version row
    unread: int = 0                              # the files of the run that could not be read now


def reading_of(runs: Sequence) -> Reading:
    """What the sheet takes of a case with these runs (the runs of runs_of of aid/evalscripts/case_runs.py, in their order): the
    unreviewed output of the run that counts, the line ids of the stored versions, the values entered in Exact that it compared
    with, and, when it has a flag, the text layer of its document and of the PO version it selected. A case without a run, or
    whose runs all waited for their purchase order, is not run. Every file is read with one read of its bytes, the PDF files too,
    and none is named in what comes back. A file of the run that cannot be read at this moment (another program holds it) is
    counted and said in a remark: the output of such a run is then not taken as kept."""
    from aid.evalscripts import case_runs                # here, so that the counts of this module import nothing of the case folders

    chosen = case_runs.counted(runs)
    remarks = [chosen.note] if chosen.note else []
    run = chosen.run
    if run is None:
        return Reading(lacks=NOT_RUN, remarks=tuple(remarks))
    output = case_runs.output_of(run)
    if run.unread:
        remarks.append(UNREAD_NOW.format(n=len(run.unread), s="" if len(run.unread) == 1 else "s"))
    if not output.kept:
        return Reading(lacks=NOT_KEPT, run=chosen.label, remarks=(*remarks, output.reason), unread=len(run.unread))
    entered = output.entered or {}
    if entered.get("unread"):
        remarks.append(ENTERED_UNREAD)
    replaced = entered.get("replaced") if isinstance(entered.get("replaced"), list) else []
    versions = _versions(run.payload("po_versions"))
    selected = versions.get(output.choice.version) if output.choice is not None else None
    flagged = output.findings is not None and bool(output.findings.flags)
    document = run.payload("read_document")
    lines = {number: frozenset(line.line_id for line in version.document.lines) for number, version in versions.items()}
    return Reading(lacks=output.lacks, choice=output.choice, pairs=output.pairs, findings=output.findings,
                   entered=frozenset((str(mark.get("line_id")), str(mark.get("field"))) for mark in replaced if isinstance(mark, dict)),
                   version_lines=lines, document=_document_text(run.folder, document) if flagged else Text(),
                   po=_po_text(selected) if flagged else Text(), run=chosen.label, remarks=tuple(remarks), unread=len(run.unread))


def _versions(payload) -> dict[int, PoVersion]:
    """The stored versions of the PO as step 01 of a run recorded them, by their number; a version in another form is left out."""
    found = {}
    for entry in payload.get("versions", []) if isinstance(payload, dict) and isinstance(payload.get("versions"), list) else []:
        try:
            version = PoVersion.model_validate(entry)
        except ValidationError:
            continue
        found[version.version] = version
    return found


def _document_text(folder, document) -> Text:
    """The text layer of the supplier's document of a case folder: the copy in its input folder that the run read (the only input
    file when the record does not say which), when its sha256 is still the one case.json holds."""
    name = document.get("source_file") if isinstance(document, dict) else None
    files = [file for file in folder.files if file[0] == name] or (list(folder.files) if len(folder.files) == 1 else [])
    if not files:
        return Text(None, "file not found")
    file, sha256 = files[0]
    return _text_layer(folder.path / "input" / file, sha256)


def _po_text(version: PoVersion | None) -> Text:
    """The text layer of the PO version the run selected: the PDF at the path that step 01 recorded, when its sha256 is still the
    one recorded there. A look from an export of the ERP has no text layer, and its values no source."""
    if version is None:
        return Text(None, "file not found")
    if version.document.reader.startswith("export") or version.pdf_path.suffix.lower() != ".pdf":
        return Text(None, "export")
    return _text_layer(version.pdf_path, version.sha256)


def _text_layer(path: Path, sha256: str | None) -> Text:
    """The text lines of a PDF, read from its bytes so that no handle on the file stays open, by page and line as every source of
    the aid counts them (extract_lines of aid/readers/pdf_text.py). There is nothing to check a source against, and the reason
    says why, for a saved page or an image, a file that is not there or cannot be read now, a file whose sha256 is not the one
    recorded at the run, and a PDF without text lines (a scan). The reason never holds the path."""
    kinds = {".pdf": "", ".html": "saved page", ".htm": "saved page", ".png": "image", ".jpg": "image", ".jpeg": "image"}
    kind = kinds.get(path.suffix.lower(), "no text layer")
    if kind:
        return Text(None, kind)
    try:
        data = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return Text(None, "file not found")
    except OSError:
        return Text(None, "file cannot be read now")
    if hashlib.sha256(data).hexdigest() != sha256:
        return Text(None, "file changed since the run")
    try:
        lines = extract_lines(io.BytesIO(data))
    except Exception:                                # whatever the PDF library raises on a file that is no PDF after all
        lines = []
    return Text({(line.page, line.line): _collapsed(line.text) for line in lines}) if lines else Text(None, "no text layer")


@dataclass(frozen=True)
class _Item:
    """One item of the aid's output: a flag, a could-not-compare item or a match."""
    kind: str                                    # "flag", "uncompared" or "match"
    item_id: str                                 # F1, U1, ...; "" for a match
    found: Flag | Uncompared | Match

    def on(self, item: ReferenceItem) -> bool:
        """Whether this item sits on the reference item: the same field, and the line of the reference item among its lines. An
        extra line is keyed by the line of the document, every other item by the PO line, required information by the case."""
        if self.found.field != item.field:
            return False
        if item.item_type == REQUIRED_ITEM:
            return True
        return item.line_id in (self.found.doc_line_ids if item.line_label == EXTRA else self.found.po_line_ids)


# --- the rows of one case ---

def sheet_rows(case_id: str, reading: Reading, references: Sequence[ReferenceItem] | None = None) -> list[dict[str, str]]:
    """The rows of one case of the sheet, each with the columns of SHEET_COLUMNS: the version row, then with references a row for
    each item of the aid on each reference item (or one row that says there is none) and a row for each item of the aid that
    belongs to no reference item, and without references a row for each item of the aid. See the module docstring for the rules."""
    items = [item for item in references or () if item.item_type != VERSION_ITEM]
    version_item = next((item for item in references or () if item.item_type == VERSION_ITEM), None)
    rows = [_version_row(case_id, reading, version_item)]
    if reading.lacks in (NOT_RUN, NOT_KEPT):
        return rows
    uncounted = _uncounted(items)
    if reading.findings is None or reading.pairs is None:
        said = NO_CASE_OUTPUT.format(why="no version selected" if reading.lacks == NO_VERSION else "failed")
        return rows + [_row(case_id, item, note=f"{NOT_LOOKED_UP}; {NOT_COUNTED}") if item in uncounted
                       else _row(case_id, item, detection_class=_class(item, "none"), note=said) for item in items]
    found = [_Item("flag" if isinstance(finding, Flag) else "uncompared", item_id, finding)
             for item_id, finding in findings_by_id(reading.findings).items()]
    found += [_Item("match", "", finding) for finding in reading.findings.matches]
    copies = _copies(reading.findings.flags)
    wrong = _wrong_version(reading, version_item)
    mapped: set[int] = set()
    for item in items:
        if item in uncounted:
            rows.append(_row(case_id, item, note=f"{NOT_LOOKED_UP}; {NOT_COUNTED}"))
            continue
        place, pair_id = _place(reading.pairs, item)
        status = _line_match(item, place)
        if item.item_type != REQUIRED_ITEM and not place:
            rows.append(_row(case_id, item, note=f"{NO_ITEM_OUTPUT}; {NOT_FOUND}"))
            continue
        on_it = [(number, aid) for number, aid in enumerate(found) if aid.on(item)]
        mapped.update(number for number, _ in on_it)
        if not on_it:
            blank = wrong and item.label != NOT_APPLICABLE
            rows.append(_row(case_id, item, line_pair_id=pair_id, line_match_status=status,
                             detection_class="" if blank else _class(item, "none"),
                             note=NO_ITEM_OUTPUT + (f"; {WRONG_VERSION}" if blank else "")))
        rows += [_aid_row(case_id, item, aid, reading, copies, wrong) | {"line_match_status": status} for _, aid in on_it]
    return rows + [_aid_row(case_id, None, aid, reading, copies, wrong, bool(references is not None))
                   for number, aid in enumerate(found) if number not in mapped]


def _row(case_id: str, item: ReferenceItem | None, **cells: str) -> dict[str, str]:
    """A row of the sheet for the case, with the columns of the reference item when it is on one, and these cells."""
    row = dict.fromkeys(SHEET_COLUMNS, "") | {"case_id": case_id}
    if item is not None:
        label = f"{item.label} ({item.line_label})" if item.item_type == PRESENCE_ITEM else item.label
        row |= {"reference_item_id": item.item_id, "field": item.field, "reference_label": label, "comparable": item.comparable}
    return row | cells


def _uncounted(items: Sequence[ReferenceItem]) -> list[ReferenceItem]:
    """The field items of a line that is wholly missing or extra, which are not counted (scoring manual, section 4): those that carry
    that line label themselves, and those of a PO line whose line-presence item is labelled missing."""
    missing = {item.line_id for item in items if item.item_type == PRESENCE_ITEM and item.line_label == "missing"}
    return [item for item in items if item.item_type == FIELD_ITEM
            and (item.line_label in ("missing", EXTRA) or (item.line_id in missing and item.line_label != EXTRA))]


def _class(item: ReferenceItem, kind: str) -> str:
    """The class that the label of a reference item gives an item of the aid of this kind ("flag", "uncompared", "match", or "none"
    for no item at all) by lookup; "" for a flag on a comparable difference, which is a judgement."""
    if item.label == NOT_APPLICABLE:
        return NOT_SCORED
    if item.label in NOT_COMPARABLE:
        return {"flag": OUTSIDE, "uncompared": JUSTIFIED, "match": UNSETTLED_MATCH, "none": NO_OUTPUT}[kind]
    if kind == "uncompared":
        return ABSTENTION
    if item.difference:
        return "" if kind == "flag" else MISS
    return {"flag": FALSE_FLAG, "match": NO_DIFFERENCE, "none": NO_OUTPUT}[kind]


# --- the version ---

def _version_row(case_id: str, reading: Reading, item: ReferenceItem | None) -> dict[str, str]:
    """The version row of a case: no field; the version item of the references when there are any; what the aid selected, by a
    fixed word for its method and never by its reason, which holds text of the document or of a model; and the run that counts."""
    choice = reading.choice
    if reading.lacks in (NOT_RUN, NOT_KEPT):
        said, status = AID + reading.lacks, reading.lacks
    elif choice is None or (reading.findings is None and choice.version is not None):
        said, status = NO_CASE_OUTPUT.format(why="failed"), NO_OUTPUT
    elif choice.version is None:
        said = f"{AID}no version ({_method(choice)})"
        status = VERSION_ABSTENTION if choice.reason == CANNOT_SELECT else NO_STORED_VERSION
    else:
        said, status = f"{AID}version {choice.version} ({_method(choice)})", ""
        if item is not None:
            status = RIGHT if choice.version == item.version else VERSION_ERROR
    if item is not None and item.version is None and status not in (NOT_RUN, NOT_KEPT, NO_OUTPUT):
        status = REFERENCE_UNRESOLVED
    row = _row(case_id, None, version_status=status, note="; ".join(part for part in (said, reading.run, *reading.remarks) if part))
    if item is not None:
        row |= {"reference_item_id": item.item_id, "reference_label": UNRESOLVED if item.version is None else f"version {item.version}"}
    return row


def _method(choice: VersionChoice) -> str:
    """A fixed word for how the aid came to its choice of a version, told from its method and the fixed beginnings of its reason
    (aid/versioning.py). The reason itself is never written: it holds a date of the document, or the text of a model."""
    if choice.method == "reference":
        if choice.reason.startswith("quotes version"):
            return "quoted version"
        if choice.reason.startswith("dated "):
            return "date"
        return "single version" if choice.reason == "single version" else "reference"
    if choice.method == "none":
        if choice.reason.startswith("quotes version"):
            return "quoted version not stored"
        return choice.reason if choice.reason in (NO_STORED_VERSION, CANNOT_SELECT) else "none"
    return choice.method


def _wrong_version(reading: Reading, item: ReferenceItem | None) -> bool:
    """Whether the classes of the case are left to the rater because the aid selected another version than the document answers,
    and the two versions are not known to hold the same line ids: an id then need not name the same line in both."""
    if item is None or item.version is None or reading.choice is None or reading.choice.version in (None, item.version):
        return False
    selected, answered = reading.version_lines.get(reading.choice.version), reading.version_lines.get(item.version)
    return selected is None or selected != answered


# --- the lines ---

def _pair_id(po_line_ids: Sequence[str], doc_line_ids: Sequence[str]) -> str:
    """The line pair in a form that no spreadsheet reads as a time or a date: "P1+2|D1", "P4|D-" for a PO line that no line
    answers, "P-|D3" for a line of the document that answers none; "" for an item of the case as a whole."""
    if not po_line_ids and not doc_line_ids:
        return ""
    return f"P{'+'.join(po_line_ids) or '-'}|D{'+'.join(doc_line_ids) or '-'}"


def _place(pairs: MatchResult, item: ReferenceItem) -> tuple[str, str]:
    """Where the aid put the line of a reference item, and the line pair: the kind of its pair, UNCERTAIN, UNMATCHED for a PO line
    that no line answers, EXTRA for a line of the document that answers none; ("", "") when the aid's reading holds no such line,
    and for an item of the case as a whole."""
    of = (lambda pair: pair.doc_line_ids) if item.line_label == EXTRA else (lambda pair: pair.po_line_ids)
    for candidates, word in ((pairs.pairs, ""), (pairs.uncertain, UNCERTAIN)):
        for pair in candidates:
            if item.line_id in of(pair):
                return word or pair.kind, _pair_id(pair.po_line_ids, pair.doc_line_ids)
    if item.line_label == EXTRA:
        return (EXTRA, _pair_id([], [item.line_id])) if item.line_id in pairs.extra_doc else ("", "")
    return (UNMATCHED, _pair_id([item.line_id], [])) if item.line_id in pairs.unmatched_po else ("", "")


def _line_match(item: ReferenceItem, place: str) -> str:
    """The column line_match_status of a line-presence item: RIGHT when the place the aid gave the line agrees with the line label
    (the kind of pair; which line it was paired with is not checked), UNCERTAIN when the aid left the line uncertain, FORCED for a
    pair on a line that the reference labels missing or unresolved, MISSED_SPLIT for a split or merged line that the aid has
    otherwise, OTHER for the rest; "" for another item and for a line the reading does not hold."""
    if item.item_type != PRESENCE_ITEM or not place:
        return ""
    if place == UNCERTAIN or place == PLACE_OF.get(item.line_label):
        return UNCERTAIN if place == UNCERTAIN else RIGHT
    if item.line_label in ("missing", UNRESOLVED) and place in PAIR_KINDS:
        return FORCED
    return MISSED_SPLIT if item.line_label in ("split", "merged") else OTHER


# --- the items of the aid ---

def _copies(flags: Sequence[Flag]) -> dict[str, str]:
    """The flags that repeat an earlier flag of the case, each with the id of the first: the same field, lines, detail, values and
    sources (scoring manual, section 4: the same item, assertion and source)."""
    def told(flag: Flag) -> tuple:
        values = tuple(None if value is None else (value.value, value.source and (value.source.page, value.source.line))
                       for value in (flag.po_value, flag.doc_value))
        return flag.field, tuple(flag.po_line_ids), tuple(flag.doc_line_ids), flag.detail, values

    first: dict[tuple, str] = {}
    return {flag.flag_id: first[told(flag)] for flag in flags if first.setdefault(told(flag), flag.flag_id) != flag.flag_id}


def _aid_row(case_id: str, item: ReferenceItem | None, aid: _Item, reading: Reading, copies: Mapping[str, str], wrong: bool,
             unmapped: bool = False) -> dict[str, str]:
    """The row of one item of the aid on a reference item, or on none (then `unmapped` says that there are references it could have
    been on). The class is the lookup's; a flag on a comparable difference has none and its note holds the proposal; a copy of a
    flag has none either; and with a wrong version every class that depends on the lines is left to the rater."""
    found = aid.found
    notes = [SAYS_SAME + _pair_kind(reading.pairs, found) if aid.kind == "match"
             else f"{AID}could not compare ({found.reason})" if aid.kind == "uncompared" else AID + found.detail]
    if any((line_id, found.field) in reading.entered for line_id in found.po_line_ids):
        notes.append(ENTERED)
    cells = {"line_pair_id": _pair_id(found.po_line_ids, found.doc_line_ids), "field": found.field, "flag_id": aid.item_id}
    traceable, sources = _traceable(found, reading) if aid.kind == "flag" else ("", "")
    cells["source_traceable"] = traceable
    if aid.item_id in copies:
        cells["duplicate_of"] = copies[aid.item_id]
        notes.append(f"duplicate of {copies[aid.item_id]}")
    elif wrong and (item is None or item.label != NOT_APPLICABLE):
        notes.append(WRONG_VERSION)
    elif item is None:
        cells["detection_class"] = UNMAPPED if unmapped else ""
    else:
        cells["detection_class"] = _class(item, aid.kind)
        if aid.kind == "flag" and item.difference and item.label not in NOT_COMPARABLE:
            notes.append(_proposal(item, found, traceable))
    return _row(case_id, item, **cells, note="; ".join(notes + [sources] if sources else notes))


def _pair_kind(pairs: MatchResult | None, found: Match) -> str:
    """For the match of a line presence, the kind of its pair in brackets, taken from the line pairs: the value of a match is never
    written, as for every other field it is a value of the documents."""
    if found.field != "line_presence" or pairs is None:
        return ""
    kind = next((pair.kind for pair in pairs.pairs
                 if (pair.po_line_ids, pair.doc_line_ids) == (found.po_line_ids, found.doc_line_ids)), "")
    return f" ({kind} pair)" if kind else ""


def _proposal(item: ReferenceItem, flag: Flag, traceable: str) -> str:
    """The proposal for a flag on a comparable difference, and what is left to judge: "true flag" when the detail of the flag agrees
    with the label, no source fails and, where the reference cites a line of the document and the flag has a document value, the
    flag cites that line; else "false flag", with the defects named. The rater confirms or corrects it: whether the stated values
    are what the documents show is never looked up, nor the line of the document where the two cannot be compared, nor a source
    that could not be checked."""
    defects = []
    if flag.detail not in AGREES[item.label]:
        defects.append(f"the flag says {flag.detail} where the reference says {item.label}")
    if traceable == "no":
        defects.append(NO_TRACE)
    compared = item.cites is not None and flag.doc_value is not None
    if compared and (flag.doc_value.source is None or (flag.doc_value.source.page, flag.doc_value.source.line) != item.cites):
        defects.append(OTHER_LINE)
    judge = ["the stated difference"] + ([] if compared else ["the document line"]) + ([] if traceable else ["the source"])
    proposed = f"{FALSE_FLAG} ({' and '.join(defects)})" if defects else TRUE_FLAG
    return f"{PROPOSAL}{proposed}; to judge: {', '.join(judge)}"


# --- the source of a flag ---

def _traceable(flag: Flag, reading: Reading) -> tuple[str, str]:
    """The column source_traceable of a flag and what the note says of it. Each side is judged on its own (_side). The flag is "no"
    when a side fails, "yes" when every side that exists was checked and cites, and "" otherwise."""
    entered = any((line_id, flag.field) in reading.entered for line_id in flag.po_line_ids)
    sides = [("PO", _side(flag.po_value, reading.po, entered)), ("document", _side(flag.doc_value, reading.document, False))]
    words = [word for _, word in sides if word]
    cell = "no" if "no" in words else "yes" if words and all(word == "yes" for word in words) else ""
    return cell, "source: " + ", ".join(f"{name} side {word}" if word else f"no {name} value" for name, word in sides)


def _side(value: DocValue | None, text: Text, entered: bool) -> str:
    """One side of a flag: "" without a value; "not checked", with the reason, when there is no text layer to check it against (a
    value entered in Exact has no text and no source at all); else "yes" when the value cites a line of the text layer that holds
    its text (the rule of aid/evalscripts/score_reading.py) and "no" when it does not, a value without a source or a text among
    them."""
    if value is None:
        return ""
    if entered or text.lines is None:
        return f"not checked ({'value entered in Exact' if entered else text.why})"
    return "yes" if _cites(value, text.lines) else "no"


# --- the two files of the sheet ---

SHEET_CSV, SHEET_XLSX = "raw_output_scores.csv", "raw_output_scores.xlsx"
# The columns of the sheet with a closed list, each with its values; a cell of one may also be empty.
CLOSED = {"detection_class": CLASSES, "source_traceable": ("yes", "no"), "comparable": ("yes", "no"),
          "version_status": VERSION_STATUSES, "line_match_status": LINE_MATCH_STATUSES}
ROOM = 200                                       # the rows below the last one that the lists of the workbook still cover


def csv_bytes(columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> bytes:
    """A CSV file of these columns and rows: UTF-8, commas, and "\\n" at the end of each line on every system."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows([row[column] for column in columns] for row in rows)
    return out.getvalue().encode("utf-8")


def sheet_csv(rows: Iterable[Mapping[str, str]]) -> bytes:
    """The sheet as raw_output_scores.csv."""
    return csv_bytes(SHEET_COLUMNS, rows)


def sheet_xlsx(rows: Sequence[Mapping[str, str]]) -> bytes:
    """The sheet as raw_output_scores.xlsx, the file for the rater: the same rows, every cell a text cell, so that a spreadsheet
    makes no time, date or number of a line pair or an id; the header row frozen; and a list to choose from in each column with a
    closed list, also for rows that the rater adds below."""
    book = Workbook()
    page = book.active
    page.title = "raw_output_scores"
    page.append(SHEET_COLUMNS)
    for row in rows:
        page.append([row[column] for column in SHEET_COLUMNS])
    for line in page.iter_rows():
        for cell in line:
            cell.number_format = "@"
    page.freeze_panes = "A2"
    for column, values in CLOSED.items():
        letter = page.cell(row=1, column=SHEET_COLUMNS.index(column) + 1).column_letter
        choice = DataValidation(type="list", formula1=f'"{",".join(values)}"', allow_blank=True)
        choice.add(f"{letter}2:{letter}{page.max_row}")
        page.add_data_validation(choice)
    for letter, width in zip("ABCDEFGHIJKLM", (12, 16, 12, 14, 34, 8, 28, 12, 16, 12, 26, 22, 110)):
        page.column_dimensions[letter].width = width
    saved = io.BytesIO()
    book.save(saved)
    book.close()
    return saved.getvalue()


# --- reading a sheet ---

_PAIR = re.compile(r"P(-|[^|]+)\|D(-|.+)")
_FLAG, _ANY_ID = re.compile(r"F[0-9]+"), re.compile(r"[FU][0-9]+")
_VERSION_LABEL = re.compile(r"version [0-9]+")
# What a spreadsheet makes of a cell that it took for a truth value or a formula, in English and in Dutch.
_NOT_TYPED = re.compile(r"TRUE|FALSE|WAAR|ONWAAR|#[A-Z]+(/[A-Z0-9]+)?[?!]?")
_PROPOSED = re.compile(re.escape(PROPOSAL) + f"({TRUE_FLAG}|{FALSE_FLAG})")
REFERENCE_LABELS = (SAME, DIFFERENT, MISSING, UNSETTLED, UNRESOLVED, NOT_APPLICABLE)


def read_sheet(data: bytes, name: str) -> list[dict[str, str]]:
    """The rows of a sheet, as the sheet command wrote it or as a rater filled it, from the bytes of a .csv or a .xlsx file, each
    with the columns of SHEET_COLUMNS. A CSV is read by the rules of the records command (UTF-8 or the Windows code page, commas or
    semicolons, cells stripped, empty rows passed over, short rows padded); of a workbook the first page is read. The cells are
    read strictly: the spaces inside a cell are folded, and the columns with a closed list, the field and the reference label are
    put in small letters, the ids of flags in capitals. Raises Refusal, with the row of the file, the column and what it may hold,
    for a value outside its list, a truth value or an error code that a spreadsheet made of a cell, a line pair or an id that does
    not read as one, a row without a case id, a note that lost its beginning, a row that stands twice, a case without exactly
    one version row, and a reference item whose rows do not agree. A typing error would otherwise change the counts without a word."""
    read = _workbook(data, name) if name.lower().endswith(".xlsx") else table(data, name, SHEET_COLUMNS, "a raw output sheet")
    rows, keys, versions, items = [], {}, {}, {}
    for number, typed in zip(read.numbers, read.rows):
        row = _checked(name, number, typed)
        rows.append(row)
        key = ((row["case_id"], row["reference_item_id"], row["flag_id"]) if row["reference_item_id"] else
               (row["case_id"], "", row["line_pair_id"], row["field"], row["flag_id"]))
        if key in keys:
            raise Refusal(f"{name}, row {number}: the same case, reference item and flag as row {keys[key]} ({', '.join(key[:3])}): "
                          "a row stands in the sheet once")
        keys[key] = number
        versions[row["case_id"]] = versions.get(row["case_id"], 0) + (not row["field"])
        if row["reference_item_id"]:
            told = (row["field"], row["reference_label"], row["comparable"])
            first, at = items.setdefault((row["case_id"], row["reference_item_id"]), (told, number))
            if told != first:
                column = next(index for index, cell in enumerate(told) if cell != first[index])
                raise Refusal(f"{name}, row {number}: the reference item {row['reference_item_id']} is {told[column]!r} here and "
                              f"{first[column]!r} in row {at} (the column {('field', 'reference_label', 'comparable')[column]})")
    for case_id, count_ in versions.items():
        if count_ != 1:
            raise Refusal(f"{name}: case {case_id} has {count_} version rows (rows without a field): a case has one")
    return rows


def _workbook(data: bytes, name: str) -> Table:
    """The first page of a workbook as a table: its first row that holds anything is the header, and a cell that is no text is
    read as what it shows (a truth value as TRUE or FALSE, a whole number without a decimal point)."""
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                # whatever the library raises on a file that is no workbook
        raise Refusal(f"{name} cannot be read as a workbook: save it as .xlsx, or as CSV UTF-8 under a name that ends in "
                      ".csv") from None
    try:
        lines = [[_shown(cell) for cell in line] for line in book.worksheets[0].iter_rows(values_only=True)]
    finally:
        book.close()
    header: list[str] | None = None
    rows, numbers = [], []
    for number, cells in enumerate(lines, start=1):
        if not any(cells):
            continue
        if header is None:
            header = cells
            continue
        cells += [""] * (len(header) - len(cells))
        rows.append({column: cell for column, cell in zip(header, cells) if column})
        numbers.append(number)
    missing = [column for column in SHEET_COLUMNS if column not in (header or [])]
    if missing:
        raise Refusal(f"{name} is not a raw output sheet: it lacks the column{'s' if len(missing) > 1 else ''} {', '.join(missing)}")
    return Table(name, tuple(column for column in header if column), rows, numbers)


def _shown(value) -> str:
    """A cell of a workbook as text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _checked(name: str, number: int, typed: Mapping[str, str]) -> dict[str, str]:
    """One row of a sheet in the form the counts read, or the Refusal that names its row, the column and what the column holds."""
    where = f"{name}, row {number}"
    row = {column: " ".join(typed[column].split()) for column in SHEET_COLUMNS}
    for column, cell in row.items():
        if _NOT_TYPED.fullmatch(cell.upper()):
            raise Refusal(f"{where}: the column {column} holds {cell!r}, which a spreadsheet made of what was typed: type the value "
                          "again as text")
    if not row["case_id"]:
        raise Refusal(f"{where} has no case id")
    lowered = {column: row[column].lower() for column in (*CLOSED, "field", "reference_label")}
    capitals = {column: row[column].upper() for column in ("flag_id", "duplicate_of")}
    for column, values in (CLOSED | {"field": FIELDS}).items():
        if lowered[column] and lowered[column] not in values:
            raise Refusal(f"{where}: the column {column} holds {row[column]!r}: it is empty or one of {', '.join(values)}")
    if row["line_pair_id"] and not _PAIR.fullmatch(row["line_pair_id"]):
        raise Refusal(f"{where}: the column line_pair_id holds {row['line_pair_id']!r}: a line pair reads as P1+2|D1, P4|D- or P-|D3")
    if capitals["flag_id"] and not _ANY_ID.fullmatch(capitals["flag_id"]):
        raise Refusal(f"{where}: the column flag_id holds {row['flag_id']!r}: a flag is F and a number, a could-not-compare item "
                      "U and a number")
    if capitals["duplicate_of"] and not _FLAG.fullmatch(capitals["duplicate_of"]):
        raise Refusal(f"{where}: the column duplicate_of holds {row['duplicate_of']!r}: it names a flag, F and a number")
    label = lowered["reference_label"]
    known = (not label or (_VERSION_LABEL.fullmatch(label) or label == UNRESOLVED if not lowered["field"] else
                           _line_label(label)[0] in REFERENCE_LABELS and _line_label(label)[1] in LINE_LABELS
                           if lowered["field"] == "line_presence" else label in REFERENCE_LABELS))
    if not known:
        raise Refusal(f"{where}: the column reference_label holds {row['reference_label']!r}: it is a label of the references as the "
                      f"sheet wrote it ({', '.join(REFERENCE_LABELS)}; with the line label in brackets for a line presence)")
    if not row["note"].lower().startswith(AID.strip()):
        raise Refusal(f"{where}: the note does not begin with '{AID.strip()}' and what the aid asserts, as the sheet wrote it: the "
                      "counts read that beginning; write a remark behind it")
    if lowered["detection_class"] in (TRUE_FLAG, FALSE_FLAG) and not _FLAG.fullmatch(capitals["flag_id"]):
        raise Refusal(f"{where}: the class {lowered['detection_class']} stands on a row without a flag (the column flag_id)")
    if lowered["detection_class"] == TRUE_FLAG and _line_label(label)[0] not in DIFFERENCES:
        raise Refusal(f"{where}: the class {TRUE_FLAG} stands on a row whose reference item is no difference (the column "
                      f"reference_label): a flag there is a {FALSE_FLAG}, or lies {OUTSIDE}")
    return row | lowered | capitals


def _line_label(reference_label: str) -> tuple[str, str]:
    """The label and the line label of the reference label of a line-presence row: "same (merged)"."""
    label, _, line_label = reference_label.partition(" (")
    return label, line_label.removesuffix(")")


# --- the counts ---

COUNTS_CSV, CASES_CSV, TOTALS_CSV = "raw_output_counts.csv", "raw_output_cases.csv", "raw_output_totals.csv"
SETS = ("made-up", "development", "held-out", "challenge")
MECHANICAL, RATER = "mechanical", "rater"
BASES = (MECHANICAL, RATER)
COUNT_COLUMNS = ("set", "classes", "field", "reference_items", "comparable_items", "compared_items", "no_output_items", "coverage",
                 "differences", "detections", "misses", "misses_by_abstention", "recall", "flags", "true_flags", "false_flags",
                 "precision", "cases_with_differences", "cases_with_flags", "flags_outside", "justified_items", "abstention_items",
                 "could_not_compare_items_of_the_aid", "matches_on_unsettled_items", "cannot_be_mapped",
                 "flags_with_a_value_without_a_traceable_source", "flags_source_not_checked", "duplicate_flags", "unscored_rows")
CASE_COLUMNS = ("set", "classes", "case_id", "output", "version_status", "line_matching_errors", "line_ids_not_found", "differences",
                "detections", "misses", "flags", "true_flags", "false_flags", "duplicate_flags", "abstention_items", "cannot_be_mapped",
                "unscored_rows")
TOTAL_COLUMNS = ("set", "classes", "quantity", "value", "denominator", "what_the_denominator_is")
UNDEFINED = "undefined"
WITHHELD = "withheld: unscored rows"
NOT_FOR_CHALLENGE = "not reported: challenge set"
SIX_FIELDS = "Six fields are counted where the protocol names five: it has quantity and unit as one field."
SET_WORDS = {"made-up": "the aid's behaviour on made-up documents; no claim about real documents",
             "development": "development cases: not part of the results",
             "held-out": "the aid's raw output on the held-out cases (claim 1)",
             "challenge": "constructed functional challenges: counts only, no recall and no precision, never pooled with the "
                          "natural cases"}
BASIS_WORDS = {MECHANICAL: "the classes are the mechanical proposals of the sheet, not scored by a rater",
               RATER: "the classes are those a rater gave; a row that is not judged yet is unscored"}
HELD_OUT_PROPOSALS = "the held-out cases by the mechanical proposals of the sheet: not the scored record of claim 1"


@dataclass(frozen=True)
class Counts:
    """What count makes of a sheet: the rows of the three files of the counts, and the lines for the terminal."""
    fields: list[dict[str, object]]              # raw_output_counts.csv: a row per field and the row total
    cases: list[dict[str, object]]               # raw_output_cases.csv: a row per case of the sheet
    totals: list[dict[str, object]]              # raw_output_totals.csv: the figures per case with their denominators
    printed: list[str]


@dataclass(frozen=True)
class _Scored:
    """One row of a sheet with what the counts take of it."""
    row: Mapping[str, str]
    scored: str                                  # its class on the basis of the counts: the cell, or the proposal of the note
    kind: str                                    # "version", "flag", "uncompared", "match", or "none" for no item of the aid
    uncounted: bool                              # a field item of a missing or an extra line: in no count
    unscored: bool                               # a row that needs a class and has none

    @property
    def item(self) -> tuple[str, str]:
        return self.row["case_id"], self.row["reference_item_id"]

    @property
    def flag(self) -> tuple[str, str]:
        return self.row["case_id"], self.row["flag_id"]


def count(rows: Sequence[Mapping[str, str]], set_name: str, classes: str) -> Counts:
    """The counts of protocol section 8.2 from the rows of a sheet (read_sheet, or sheet_rows) and nothing else. `classes` is the
    basis: RATER takes the class of every row as it stands in the sheet; MECHANICAL takes the proposal of the note for a row
    without one. The set and the basis go into every row. See the module docstring for what each column counts."""
    scored = [_scored(row, classes) for row in rows]
    of_fields = [row for row in scored if row.kind != "version"]
    pending = sum(row.unscored for row in of_fields)
    withheld = NOT_FOR_CHALLENGE if set_name == "challenge" else WITHHELD if pending else ""
    front = {"set": set_name, "classes": classes}
    fields = [front | {"field": name} | _figures(_tally([row for row in of_fields if row.row["field"] == name]), withheld)
              for name in FIELDS]
    total = _figures(_tally(of_fields), withheld)
    fields.append(front | {"field": "total"} | total)
    cases, case_ids = [], list(dict.fromkeys(row.row["case_id"] for row in scored))
    for case_id in case_ids:
        own = [row for row in scored if row.row["case_id"] == case_id]
        status = next((row.row["version_status"] for row in own if row.kind == "version"), "")
        tally = _tally([row for row in own if row.kind != "version"])
        cases.append(front | {"case_id": case_id, "output": status if status in (NO_OUTPUT, NOT_RUN, NOT_KEPT) else "yes",
                              "version_status": status, "line_matching_errors": len(_lines(own, (FORCED, MISSED_SPLIT))),
                              "line_ids_not_found": sum(NOT_FOUND in row.row["note"].lower() for row in own)}
                     | {column: _size(tally)[column] for column in CASE_COLUMNS[7:]})
    totals = [front | dict(zip(TOTAL_COLUMNS[2:], row)) for row in _totals(scored, cases)]
    return Counts(fields, cases, totals, _printed(set_name, classes, total, {row["quantity"]: row for row in totals}))


def _scored(row: Mapping[str, str], classes: str) -> _Scored:
    """A row of the sheet with its class on the basis of the counts and the kind of item of the aid it holds."""
    note = row["note"].lower()
    given = row["detection_class"]
    proposed = _PROPOSED.search(note) if classes == MECHANICAL and not given and not row["duplicate_of"] else None
    scored = proposed[1] if proposed else given
    kind = ("version" if not row["field"] else "flag" if row["flag_id"].startswith("F") else "uncompared" if row["flag_id"] else
            "match" if note.startswith(SAYS_SAME) else "none")
    uncounted = NOT_COUNTED in note
    return _Scored(row, scored, kind, uncounted, kind != "version" and not scored and not row["duplicate_of"] and not uncounted)


def _tally(rows: Sequence[_Scored]) -> dict[str, object]:
    """The counts of some rows of a sheet (those of a field, of a case, or all): each a number, or the set of what is counted.

    Reference items are counted by their id and flags and could-not-compare items by their case and id, so that an item of the aid
    on several reference items counts once. A comparable reference difference counts once: as a detection when one of its rows is a
    true flag, as a miss when none is and none of its rows waits for a class. A flag counts once, as true when one of its rows is a
    true flag and else as false when one is a false flag, with or without a reference item; its copies are not flags. The field
    items of a missing or an extra line are reference items and are in no other count."""
    items: dict[tuple, list[_Scored]] = {}
    flags: dict[tuple, list[_Scored]] = {}
    for row in rows:
        if row.row["reference_item_id"]:
            items.setdefault(row.item, []).append(row)
        if row.kind == "flag":
            flags.setdefault(row.flag, []).append(row)

    def of_items(test, among=None) -> set:
        return {key for key in (items if among is None else among) if test(items[key])}

    def with_class(word: str, among=None) -> set:
        return of_items(lambda own: any(row.scored == word for row in own), among)

    counted = of_items(lambda own: not any(row.uncounted for row in own))
    comparable = of_items(lambda own: own[0].row["comparable"] == "yes", counted)
    differences = of_items(lambda own: _line_label(own[0].row["reference_label"])[0] in DIFFERENCES, comparable)
    detections = with_class(TRUE_FLAG, differences)
    misses = of_items(lambda own: not any(row.unscored for row in own), differences - detections)
    copies = {key for key, own in flags.items() if any(row.row["duplicate_of"] for row in own)}
    true_flags = {key for key, own in flags.items() if any(row.scored == TRUE_FLAG for row in own)} - copies
    false_flags = {key for key, own in flags.items() if any(row.scored == FALSE_FLAG for row in own)} - copies - true_flags
    outside = {key for key, own in flags.items() if any(row.scored == OUTSIDE for row in own)} - copies - true_flags - false_flags
    originals = flags.keys() - copies
    return {"reference_items": len(items), "comparable_items": len(comparable),
            "compared_items": len(of_items(lambda own: any(row.kind in ("flag", "match") for row in own), comparable)),
            "no_output_items": len(of_items(lambda own: all(row.kind == "none" for row in own), comparable)),
            "differences": differences, "detections": detections, "misses": misses,
            "misses_by_abstention": with_class(ABSTENTION, misses),
            "true_flags": true_flags, "false_flags": false_flags, "flags": true_flags | false_flags,
            "flags_outside": len(outside), "justified_items": len(with_class(JUSTIFIED)), "abstention_items": with_class(ABSTENTION),
            "could_not_compare_items_of_the_aid": len({row.flag for row in rows if row.kind == "uncompared"}),
            "matches_on_unsettled_items": len(with_class(UNSETTLED_MATCH)),
            "cannot_be_mapped": sum(row.scored == UNMAPPED for row in rows),
            "flags_with_a_value_without_a_traceable_source": sum(
                any(row.row["source_traceable"] == "no" for row in flags[key]) for key in originals),
            "flags_source_not_checked": sum(all(not row.row["source_traceable"] for row in flags[key]) for key in originals),
            "duplicate_flags": len(copies), "unscored_rows": sum(row.unscored for row in rows)}


def _size(tally: Mapping[str, object]) -> dict[str, int]:
    """A tally as numbers: of each count that is a set, how many it holds."""
    return {column: len(value) if isinstance(value, set) else value for column, value in tally.items()}


def _ratio(part: int, whole: int) -> str:
    """A ratio as "6 of 7", which no spreadsheet reads as another number, and UNDEFINED over nothing."""
    return f"{part} of {whole}" if whole else UNDEFINED


def _figures(tally: Mapping[str, object], withheld: str) -> dict[str, object]:
    """The columns of raw_output_counts.csv behind the field, from a tally. `withheld` says why no recall and no precision is
    written, when none is."""
    size = _size(tally)
    return {column: size.get(column) for column in COUNT_COLUMNS[3:]} | {
        "coverage": _ratio(size["compared_items"], size["comparable_items"]),
        "recall": withheld or _ratio(size["detections"], size["differences"]),
        "precision": withheld or _ratio(size["true_flags"], size["flags"]),
        "cases_with_differences": len({case_id for case_id, _ in tally["differences"]}),
        "cases_with_flags": len({case_id for case_id, _ in tally["flags"]})}


def _lines(rows: Sequence[_Scored], statuses: Sequence[str] = LINE_MATCH_STATUSES, labels: Sequence[str] | None = None) -> set:
    """The line-presence reference items among these rows whose line the aid placed with one of these statuses, of these line
    labels when given: each line once."""
    return {row.item for row in rows if row.row["line_match_status"] in statuses and row.row["reference_item_id"]
            and (labels is None or _line_label(row.row["reference_label"])[1] in labels)}


def _totals(scored: Sequence[_Scored], cases: Sequence[Mapping[str, object]]) -> list[tuple]:
    """The rows of raw_output_totals.csv: a quantity, its value, its denominator and what the denominator is. A case that was not
    run, or whose first output is not kept, is in the number of cases and outside every denominator."""
    of_sheet = "cases of the sheet"
    with_output = [case for case in cases if case["output"] == "yes"]
    answered = [case for case in cases if case["version_status"] in (RIGHT, VERSION_ERROR, VERSION_ABSTENTION, NO_STORED_VERSION)]
    on_version = "cases with a settled version reference in which the aid came to the version"
    rows = [("cases", len(cases), "", ""), ("cases with output", len(with_output), len(cases), of_sheet)]
    rows += [(quantity, sum(case["output"] == word for case in cases), len(cases), of_sheet) for quantity, word in (
        ("cases without output (the run failed)", NO_OUTPUT), ("cases not run", NOT_RUN),
        ("cases whose first output is not kept", NOT_KEPT))]
    rows += [(quantity, sum(case["version_status"] == word for case in cases), len(answered), on_version) for quantity, word in (
        ("version-selection errors", VERSION_ERROR), ("abstentions on the version", VERSION_ABSTENTION),
        ("cases without a stored version", NO_STORED_VERSION))]
    unresolved = sum(case["version_status"] == REFERENCE_UNRESOLVED for case in cases)
    rows.append(("cases with an unresolved version reference", unresolved, len(cases), of_sheet))
    po_lines = [label for label in LINE_LABELS if label != EXTRA]
    placed = len(_lines(scored, labels=po_lines))
    forced, missed = _lines(scored, (FORCED,)), _lines(scored, (MISSED_SPLIT,))
    rows += [("forced matches", len(forced), len(_lines(scored, labels=("missing", UNRESOLVED))),
              "PO lines that the reference labels missing or unresolved, of those the aid placed"),
             ("missed splits or merges", len(missed), len(_lines(scored, labels=("split", "merged"))),
              "PO lines that the reference labels split or merged, of those the aid placed"),
             ("line-matching errors", len(forced | missed), placed, "PO lines with a line label that the aid placed"),
             ("lines left uncertain", len(_lines(scored, (UNCERTAIN,), po_lines)), placed,
              "PO lines with a line label that the aid placed"),
             ("other line-match differences", len(_lines(scored, (OTHER,))), len(_lines(scored)),
              "lines with a line label that the aid placed, the extra lines of the document included")]
    burden = sorted(case["false_flags"] for case in with_output)
    over, middle = "cases with output", len(burden) // 2
    median = "" if not burden else burden[middle] if len(burden) % 2 or burden[middle - 1] == burden[middle] else (
        f"between {burden[middle - 1]} and {burden[middle]}")
    rows += [("false flags (the mean per case is the value over the denominator)", sum(burden), len(burden), over),
             ("false flags per case: median", median, len(burden), over),
             ("false flags per case: lowest", burden[0] if burden else "", len(burden), over),
             ("false flags per case: highest", burden[-1] if burden else "", len(burden), over),
             ("cases with a false flag", sum(1 for flags in burden if flags), len(burden), over),
             ("duplicate flags (the mean per case is the value over the denominator)",
              sum(case["duplicate_flags"] for case in with_output), len(burden), over)]
    wrong = {case["case_id"] for case in cases if case["version_status"] == VERSION_ERROR}
    size = _size(_tally([row for row in scored if row.kind != "version" and row.row["case_id"] in wrong]))
    group = "cases with a version-selection error: "
    rows += [(group + "differences", size["differences"], "", ""),
             (group + "detections", size["detections"], size["differences"], "their comparable reference differences"),
             (group + "misses", size["misses"], size["differences"], "their comparable reference differences"),
             (group + "flags", size["flags"], "", ""),
             (group + "true flags", size["true_flags"], size["flags"], "their distinct flags on comparable items"),
             (group + "false flags", size["false_flags"], size["flags"], "their distinct flags on comparable items"),
             (group + "unscored rows", size["unscored_rows"], "", ""),
             ("fields counted", len(FIELDS), len(FIELDS) - 1,
              "the fields the protocol names: it has quantity and unit as one field (section 2), which are counted apart here")]
    return rows


def _printed(set_name: str, classes: str, total: Mapping[str, object], totals: Mapping[str, Mapping[str, object]]) -> list[str]:
    """The lines for the terminal: the set and the basis first, then the cases, the version, the lines, the unscored rows and the
    totals. No time, no path, no interval."""
    def of(quantity: str) -> str:
        return f"{totals[quantity]['value']} of {totals[quantity]['denominator']}"

    def value(quantity: str) -> object:
        return totals[quantity]["value"]

    words = SET_WORDS[set_name] + "; " + BASIS_WORDS[classes]
    if (set_name, classes) == ("held-out", MECHANICAL):
        words = HELD_OUT_PROPOSALS
    rates = "" if set_name == "challenge" else f"recall {total['recall']}; precision {total['precision']}; "
    return [f"Set {set_name}, classes {classes}: {words}.",
            f"Cases: {value('cases')}; without output: {value('cases without output (the run failed)')}; "
            f"not run: {value('cases not run')}; first output not kept: {value('cases whose first output is not kept')}.",
            f"Version-selection errors: {of('version-selection errors')}; "
            f"abstentions on the version: {of('abstentions on the version')}; "
            f"no stored version: {of('cases without a stored version')}.",
            f"Line-matching errors: {of('line-matching errors')} lines; lines left uncertain: {value('lines left uncertain')}.",
            f"Unscored rows: {total['unscored_rows']}.",
            f"Total: {total['differences']} differences, {total['detections']} detected, {total['misses']} missed "
            f"({total['misses_by_abstention']} by abstention); {total['flags']} flags, {total['true_flags']} true, "
            f"{total['false_flags']} false; {rates}coverage {total['coverage']}.",
            SIX_FIELDS]


def counts_files(counts: Counts) -> dict[str, bytes]:
    """The three files of the counts, by their names."""
    return {COUNTS_CSV: csv_bytes(COUNT_COLUMNS, counts.fields), CASES_CSV: csv_bytes(CASE_COLUMNS, counts.cases),
            TOTALS_CSV: csv_bytes(TOTAL_COLUMNS, counts.totals)}


# --- the two subcommands of the records command ---

SHEET_MANIFEST, COUNTS_MANIFEST = "sheet_manifest.json", "counts_manifest.json"
UNCONFIRMED = f"The set could not be confirmed from the sheet: no {SHEET_MANIFEST} stands beside it."
KINDS = ("confirmation", "cart")                 # the control points; one sheet holds one (protocol section 2: never pooled)
PROPOSALS_NOTE = ("the classes of the lookup rows are filled; a flag on a comparable difference has no class and a proposal in its "
                  "note: a rater confirms or corrects every such row before counts --classes rater")
ABANDONED_NOTE = "attempts abandoned on a service failure leave no record in a case folder"
SOURCES_NOTE = "the source is checked for the values of flags only: the values the aid shows as matching are not checked"
RESTRICTED = "The sheet is a record of restricted storage: only counts may go to the repository."
ORDER_OF_WORK = ("Held-out cases (protocol section 7.1): the sheet is made only after the references are locked, and for the cases "
                 "handled by hand only after all timed handling is finished.")


def _records():
    """The module of the records command, which holds what its subcommands share: check_out, expand and write_manifest. It is
    imported when a subcommand runs, as that module imports this one."""
    from aid.evalscripts import records
    return records


def add_sheet_arguments(parser: argparse.ArgumentParser) -> None:
    """The arguments of the subcommand sheet."""
    parser.add_argument("--out", type=Path, required=True, metavar="DIR",
                        help="the folder the sheet is written into: a new folder outside the aid's data folder")
    parser.add_argument("--set", required=True, choices=SETS, help="the set the cases are of; it goes into the manifest")
    parser.add_argument("--case-list", required=True, metavar="FILE", help="the key between the case ids and the purchase orders")
    parser.add_argument("--cases-root", required=True, type=Path, metavar="DIR",
                        help="the cases folder of the aid's data folder, which is only read")
    parser.add_argument("--kind", choices=KINDS, default=KINDS[0], help="the control point of the sheet (default: confirmation)")
    parser.add_argument("--references", metavar="FILE",
                        help="the references (evaluation/templates/references.csv); required for held-out and challenge cases")
    parser.add_argument("--references-locked", metavar="YYYY-MM-DD",
                        help="the day the references were locked: the statement that they are, required for held-out cases and "
                             "kept in the manifest")
    parser.add_argument("--allow-not-run", action="store_true",
                        help="give a case without a run one row that says so, and do not refuse it")
    parser.add_argument("--while-running", action="store_true", help="read the case folders also while the aid runs on them")


def add_counts_arguments(parser: argparse.ArgumentParser) -> None:
    """The arguments of the subcommand counts."""
    parser.add_argument("--out", type=Path, required=True, metavar="DIR", help="the folder the counts are written into")
    parser.add_argument("--set", required=True, choices=SETS, help="the set the sheet is of; it goes into every row of the counts")
    parser.add_argument("--classes", required=True, choices=BASES,
                        help="the basis of the classes: rater takes every class as the sheet holds it; mechanical takes the "
                             "proposal of the note where a row has none")
    parser.add_argument("--sheet", required=True, metavar="FILE", help="the sheet, .csv or .xlsx")


def run_sheet(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """The subcommand sheet: read the case list, the references and the case folders, and write raw_output_scores.csv,
    raw_output_scores.xlsx and their manifest into --out. Everything that stands in the way is refused through `parser` before
    anything is written; the case folders are only read."""
    from aid.evalscripts import case_runs                # here, so that the counts of this module import nothing of the case folders

    records = _records()
    locked = _order_of_work(args, parser)
    case_list = _one(records, parser, args.case_list, "--case-list")
    references = _one(records, parser, args.references, "--references")
    if not Path(args.cases_root).is_dir():
        parser.error("--cases-root is no folder: give the cases folder of the aid's data folder")
    out = records.check_out(parser, args.out, args.cases_root, (SHEET_CSV, SHEET_XLSX, SHEET_MANIFEST),
                            restricted=args.set != "made-up")
    inputs: list = [case_list]
    try:
        cases = [case for case in case_runs.read_case_list(case_list) if case.kind == args.kind]
    except (Refusal, case_runs.Refusal) as refusal:
        parser.error(f"--case-list: {refusal}")
    if not cases:
        parser.error(f"--case-list: {case_list.name} holds no case of the kind {args.kind}")
    answers = None
    if references is not None:
        try:
            data = read_bytes(references)
            answers = read_references(data, references.name)
        except Refusal as refusal:
            parser.error(f"--references: {refusal}")
        inputs.append((references.name, data))
        without = [case.case_id for case in cases if case.case_id not in answers]
        if without:
            parser.error(f"--references: {references.name} holds no item of {_cases(len(without))} of the case list: "
                         + ", ".join(without))
    if not args.while_running and case_runs.aid_running(args.cases_root):
        parser.error(case_runs.AID_RUNS)
    checkable = args.while_running or case_runs.aid_checkable(args.cases_root)
    found = case_runs.folders(args.cases_root)
    runs = {case.case_id: case_runs.runs_of(case, found) for case in cases}
    unclear = [f"case {case_id}: {own.reason}" for case_id, own in runs.items() if own.reason and own.reason != case_runs.NO_RUN]
    if unclear:
        parser.error("--case-list: " + "; ".join(unclear))
    readings = {case_id: reading_of(own.runs) for case_id, own in runs.items()}
    not_run = [case_id for case_id, reading in readings.items() if reading.lacks == NOT_RUN]
    if not_run and not args.allow_not_run:
        parser.error(f"no run of the aid found for {_cases(len(not_run))} of the case list: {', '.join(not_run)}: run them first, "
                     "check their purchase order numbers in the case list, or give --allow-not-run")
    rows = [row for case in cases
            for row in sheet_rows(case.case_id, readings[case.case_id], None if answers is None else answers[case.case_id])]
    held = sum(reading.unread for reading in readings.values())
    options = {"set": args.set, "kind": args.kind, "case_list": case_list, "cases_root": Path(args.cases_root),
               "references": references, "references_locked": locked, "allow_not_run": args.allow_not_run,
               "while_running": args.while_running}
    try:
        records.write_manifest(out, SHEET_MANIFEST, subcommand="sheet", options=options, inputs=inputs,
                               outputs={SHEET_CSV: sheet_csv(rows), SHEET_XLSX: sheet_xlsx(rows)}, cases=len(cases), set=args.set,
                               kind=args.kind, statements={"references_locked": locked} if locked else {}, cases_not_run=len(not_run),
                               case_folders_not_read=found.unreadable, files_not_read=held, notes=[PROPOSALS_NOTE, ABANDONED_NOTE])
    except Refusal as refusal:
        parser.error(str(refusal))
    print(f"Sheet of the set {args.set}, control point {args.kind}: {_cases(len(cases))}, {len(rows)} rows, "
          f"{'against the references' if answers is not None else 'without references: no row has a class'}.")
    if not_run:
        print(f"Not run: {_cases(len(not_run))}, each with one row; the counts keep them out of every denominator.")
    if found.unreadable:
        print(f"{found.unreadable} case folder{'s' if found.unreadable > 1 else ''} could not be read now and "
              f"{'were' if found.unreadable > 1 else 'was'} passed over.")
    if held:
        print(f"{held} file{'s' if held > 1 else ''} of the runs could not be read now: the sheet says so on the version row of the "
              "case; stop the aid and run again into a new folder.")
    print(PROPOSALS_NOTE[0].upper() + PROPOSALS_NOTE[1:] + ".")
    print(RESTRICTED)
    if not checkable:
        print(case_runs.UNCHECKED_RUNNING)
    if args.set == "held-out":
        print(ORDER_OF_WORK)
    print(f"Written into {args.out}: {', '.join((SHEET_CSV, SHEET_XLSX, SHEET_MANIFEST))}")
    return 0


def run_counts(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """The subcommand counts: read the sheet strictly and write raw_output_counts.csv, raw_output_cases.csv, raw_output_totals.csv
    and their manifest into --out. It reads the sheet and nothing else: no case folder, no case list."""
    records = _records()
    sheet = _one(records, parser, args.sheet, "--sheet")
    out = records.check_out(parser, args.out, None, (COUNTS_CSV, CASES_CSV, TOTALS_CSV, COUNTS_MANIFEST), restricted=False)
    try:
        data = read_bytes(sheet)
        rows = read_sheet(data, sheet.name)
    except Refusal as refusal:
        parser.error(f"--sheet: {refusal}")
    if not rows:
        parser.error(f"--sheet: {sheet.name} holds no row")
    made = _sheet_made(parser, sheet, args.set)
    counted = count(rows, args.set, args.classes)
    files = counts_files(counted)
    notes = [counted.printed[0], SIX_FIELDS, SOURCES_NOTE, ABANDONED_NOTE] + ([] if made is not None else [UNCONFIRMED])
    try:
        records.write_manifest(out, COUNTS_MANIFEST, subcommand="counts",
                               options={"set": args.set, "classes": args.classes, "sheet": sheet}, inputs=[(sheet.name, data)],
                               outputs=files, cases=len(counted.cases), set=args.set, classes=args.classes, sheet_manifest=made,
                               notes=notes)
    except Refusal as refusal:
        parser.error(str(refusal))
    print("\n".join(counted.printed))
    if made is None:
        print(UNCONFIRMED)
    print(f"Written into {args.out}: {', '.join((*files, COUNTS_MANIFEST))}")
    return 0


def _sheet_made(parser: argparse.ArgumentParser, sheet: Path, set_name: str) -> dict | None:
    """What the sheet_manifest.json beside the sheet says the sheet was made of: its set, its control point, the statements and the
    sha256 of the manifest, for the manifest of the counts; None when no manifest that can be read stands beside the sheet, in
    which case the set is as --set gives it and the counts say that it could not be confirmed. A --set that differs from the set
    of the manifest is refused, and so is a held-out sheet whose manifest states no day on which the references were locked: a
    case is never counted in a set it was not made for. A sheet that a rater saved again beside it under another name is still
    that sheet."""
    try:
        data = read_bytes(sheet.parent / SHEET_MANIFEST)
        made = json.loads(data)
    except (Refusal, ValueError):
        return None
    if not isinstance(made, dict) or made.get("subcommand") != "sheet" or not isinstance(made.get("set"), str):
        return None
    if made["set"] != set_name:
        parser.error(f"--set {set_name} is not the set of the sheet: the {SHEET_MANIFEST} beside it says {made['set']}; give "
                     f"--set {made['set']}")
    statements = made.get("statements") if isinstance(made.get("statements"), dict) else {}
    if set_name == "held-out" and not statements.get("references_locked"):
        parser.error(f"--set held-out: the {SHEET_MANIFEST} beside it states no day on which the references were locked: make the "
                     "sheet again with --references-locked")
    return {"name": SHEET_MANIFEST, "sha256": hashlib.sha256(data).hexdigest(), "set": made["set"], "kind": made.get("kind"),
            "statements": statements}


def _order_of_work(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    """Refuse a sheet that the order of work of the protocol does not allow, and give the day the references were locked as it
    was stated, "" without the statement. For held-out cases the references and the statement that they are locked are required
    (the aid's output stays closed while the references are built); for the challenge set the references are."""
    if args.set == "held-out" and not (args.references and args.references_locked):
        parser.error("--set held-out needs --references and --references-locked YYYY-MM-DD: the sheet of held-out cases is made only "
                     "after the references are locked")
    if args.set == "challenge" and not args.references:
        parser.error("--set challenge needs --references: the expected outcome of every variant")
    if args.references_locked and not args.references:
        parser.error("--references-locked needs --references")
    if not args.references_locked:
        return ""
    try:
        return date.fromisoformat(args.references_locked.strip()).isoformat()
    except ValueError:
        parser.error("--references-locked is the day the references were locked, as YYYY-MM-DD")


def _one(records, parser: argparse.ArgumentParser, given: str | None, argument: str) -> Path | None:
    """The one file of a file argument, with a star or a question mark expanded by the command; None when it was not given."""
    if given is None:
        return None
    files = records.expand(parser, given, argument)
    if len(files) != 1:
        parser.error(f"{argument}: {len(files)} files match {Path(given).name}: give one file")
    return files[0]


def _cases(number: int) -> str:
    return f"{number} case{'' if number == 1 else 's'}"
