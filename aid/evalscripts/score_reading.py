"""Scoring a reading against the made-up spec: how many fields a reader got right, and how many of those it cites correctly.

The reference. expected_fields(spec_path) gives the value each field of the document of a made-up spec should read as, as
{(line_id, field): normalised value}. The seven header fields (po_reference, version_hint, supplier, document_date, currency,
incoterm, capture_date) stand under the line id "doc", and each line of the document has its nine fields (item_code, description,
quantity, unit, unit_price, price_basis, discount_pct, net_price, delivery_date) under its own line id. A value the document does
not print is None: a cart has no document_date, version_hint or incoterm, a confirmation has no capture_date, a spec gives no
net_price, and so on. The values come from MadeUpSpec and agree with document_from_spec (the perfect reading, aid/tests/helpers.py),
so that the perfect reading of each of the 22 cases scores a field accuracy of 1.0.

The score. score_reading(doc, expected, lines) counts every entry of `expected` once.
- A field is right when the value of the reading equals the expected value. None equals a missing value, and such a field is also
  counted in missing_marked_correctly; a value where the document shows none, or no value where it shows one, is wrong.
- A line the reading lacks makes all its fields wrong, those the document leaves out too: nothing was read there to be marked missing.
- A line of the reading whose line_id is not among the expected lines, or that repeats a line_id already read (the first one is
  scored), is one error with the field "extra_line", counted in fields_total.
- `errors` holds one dict per wrong field, {"line_id", "field", "expected", "got"}. The error of a field of a missing line also
  has "note": "line missing". The error of an extra line has the line's item code as `got`.

The sources. When `lines` (the text lines of the PDF, from aid.readers.pdf_text.extract_lines) is not empty, each expected value that
is not None and that the reading got right is a source to check: sources_total counts them and sources_correct those whose source
is right. A source is right when the reading cites a (page, line) that exists in `lines` and that line's text holds the text the
reading gives for the value, with runs of white space as one space and accented letters in one composed form, the case kept. The
text the source quotes is not looked at. A value without a source, or without a text, has no right source. A scan, a screenshot or
an HTML page has no text lines: with `lines` empty no source is scored, sources_total is 0 and source_accuracy is None.

Nothing here assumes the LLM reader: the Document of any reader, the parser baseline included, is scored the same way.

The decision. choose(rows) is the bake-off's rule: of the models whose field accuracy and source accuracy are both within 2
percentage points of the best value of each, the cheapest.
"""
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

from aid.madeup.generate import MadeUpSpec
from aid.readers.pdf_text import TextLine
from aid.schema import DocLine, DocValue, Document

DOC = "doc"                                                         # the line id of the header fields
HEADER_FIELDS = tuple(name for name, field in Document.model_fields.items() if field.annotation is DocValue)
LINE_FIELDS = tuple(name for name, field in DocLine.model_fields.items() if field.annotation is DocValue)
MARGIN = 0.02                                                       # the decision's "within 2 percentage points"


def expected_fields(spec_path: Path) -> dict[tuple[str, str], str | None]:
    """(line_id, field) -> the normalised value the document of the spec at `spec_path` (a confirmation or a cart) should read as,
    None for a value it does not print. The header fields are under line_id "doc"; see the module docstring."""
    spec = MadeUpSpec.model_validate_json(Path(spec_path).read_text(encoding="utf-8"))
    po, confirmation = spec.po, spec.confirmation
    header = {"po_reference": po.po_number, "supplier": po.supplier, "currency": po.currency}
    if confirmation:
        quoted = confirmation.version_quoted
        header |= {"version_hint": None if quoted is None else str(quoted), "document_date": confirmation.document_date.isoformat(),
                   "incoterm": confirmation.incoterm}
    else:
        header["capture_date"] = spec.cart.capture_date.isoformat()
    expected = {(DOC, name): header.get(name) for name in HEADER_FIELDS}
    for line in spec.document.lines:
        expected |= {(line.line_id, name): getattr(line, name, None) for name in LINE_FIELDS}
    return expected


class ReadingScore(BaseModel):
    """What score_reading counts for one document."""
    fields_total: int = 0                      # the expected entries, and one more for each extra line
    fields_correct: int = 0
    sources_total: int = 0                     # the expected values that are printed and were read right, where there is a text layer
    sources_correct: int = 0
    missing_marked_correctly: int = 0          # the right fields among those the document leaves out
    errors: list[dict[str, str | None]] = Field(default_factory=list)

    @property
    def field_accuracy(self) -> float:
        """The share of the fields that are right; 0.0 for a score without fields."""
        return self.fields_correct / self.fields_total if self.fields_total else 0.0

    @property
    def source_accuracy(self) -> float | None:
        """The share of the sources to check that are right; None when there are none (no text layer, or nothing read right)."""
        return self.sources_correct / self.sources_total if self.sources_total else None


def score_reading(doc: Document, expected: dict[tuple[str, str], str | None], lines: list[TextLine]) -> ReadingScore:
    """Score the reading `doc` against `expected` (expected_fields), with `lines` the text lines of its PDF, or [] for a document
    that has none. See the module docstring for what is counted."""
    cited = {(line.page, line.line): _collapsed(line.text) for line in lines}
    expected_ids = {line_id for line_id, _ in expected} - {DOC}
    read_lines: dict[str, DocLine] = {}
    extra: list[DocLine] = []
    for line in doc.lines:
        if line.line_id in expected_ids and line.line_id not in read_lines:
            read_lines[line.line_id] = line
        else:
            extra.append(line)
    score = ReadingScore()
    for (line_id, name), want in expected.items():
        owner = doc if line_id == DOC else read_lines.get(line_id)
        score.fields_total += 1
        if owner is None:
            score.errors.append({"line_id": line_id, "field": name, "expected": want, "got": None, "note": "line missing"})
            continue
        value: DocValue = getattr(owner, name)
        if value.value != want:
            score.errors.append({"line_id": line_id, "field": name, "expected": want, "got": value.value})
            continue
        score.fields_correct += 1
        if want is None:
            score.missing_marked_correctly += 1
        elif cited:
            score.sources_total += 1
            if _cites(value, cited):
                score.sources_correct += 1
    for line in extra:
        score.fields_total += 1
        score.errors.append({"line_id": line.line_id, "field": "extra_line", "expected": None, "got": line.item_code.value})
    return score


def score_unread(expected: dict[tuple[str, str], str | None]) -> ReadingScore:
    """The score of a document that could not be read: every expected field is wrong, whatever the document leaves out, and no source
    is scored. An empty reading would not do, because it gets the fields the document leaves out right."""
    return ReadingScore(fields_total=len(expected), errors=[
        {"line_id": line_id, "field": name, "expected": want, "got": None, "note": "document not read"}
        for (line_id, name), want in expected.items()])


def _cites(value: DocValue, cited: dict[tuple[int, int], str]) -> bool:
    """Whether the value cites a line of the text layer (`cited`: (page, line) -> its collapsed text) that holds the value's text."""
    if value.source is None or value.text is None:
        return False
    line, text = cited.get((value.source.page, value.source.line)), _collapsed(value.text)
    return line is not None and bool(text) and text in line


def _collapsed(text: str) -> str:
    """The text with runs of white space as one space and accented letters in one composed form; the case is kept."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def choose(rows: list[tuple[str, float, float, float]]) -> str:
    """The model to use: of the rows (model, field accuracy, source accuracy, cost per case) whose field accuracy and source accuracy
    are both within 2 percentage points of the best value of each, the cheapest; of equal cost, the first one given. Raises ValueError
    for no rows, and for rows of which none is within 2 points of both bests (one model best on fields and another on sources, far apart)."""
    if not rows:
        raise ValueError("no model to choose from")
    best_field = max(row[1] for row in rows)
    best_source = max(row[2] for row in rows)
    # "Within 2 percentage points" counts the edge in; the small allowance keeps a gap of exactly 0.02 that floating point writes
    # as 0.020000000000000018 inside.
    within = [row for row in rows if best_field - row[1] <= MARGIN + 1e-9 and best_source - row[2] <= MARGIN + 1e-9]
    if not within:
        raise ValueError("no model is within 2 percentage points of the best field accuracy and of the best source accuracy")
    return min(within, key=lambda row: row[3])[0]
