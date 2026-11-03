"""The document schema of the checking aid, and the functions that bring the values on a document to one form.

Every reader (the PO PDF reader, the parser, the LLM reader, the cart reader) produces a Document and every
later step reads one. A DocValue keeps the text as printed beside the normalised value, so that a flag can
always show what the document says. A value that is not on the document, or that could not be read as a
value, has value None: a missing value is missing, not zero.

The normal forms of DocValue.value:
    numbers      plain decimal strings with a point and no thousands mark: "1234.50", "12.5"
    price basis  "1", "100" or "1000" (any whole number above zero is kept as it is)
    unit         "pc", "m", "kg" or "l"; any other unit as its lower-cased text
    dates        "2026-11-16"; a week "2026-W47"; a range "2026-11-16/2026-11-20"
    currency     the ISO code: "EUR", "USD" or "GBP"

Each normaliser is a pure function. It returns None for a text it cannot read, so that a reader can keep
the text and mark the value uncertain. Where a text has two possible readings, a stated rule settles it: a
number such as "1.234" (see normalize_number) and a date such as "03/04/2026", read day first (see normalize_date).
"""
import re
import unicodedata
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    page: int                     # 1-based
    line: int                     # the line of the page the text was found on
    text: str                     # the exact text found there


class DocValue(BaseModel):
    value: str | None = None      # normalised form (see the module docstring); None = missing
    text: str | None = None       # the original text on the document
    source: SourceRef | None = None
    uncertain: bool = False

    @property
    def missing(self) -> bool:
        """True when there is no normalised value: not on the document, or not readable as a value."""
        return self.value is None


class DocLine(BaseModel):
    line_id: str
    item_code: DocValue = Field(default_factory=DocValue)
    description: DocValue = Field(default_factory=DocValue)
    quantity: DocValue = Field(default_factory=DocValue)
    unit: DocValue = Field(default_factory=DocValue)
    unit_price: DocValue = Field(default_factory=DocValue)
    price_basis: DocValue = Field(default_factory=DocValue)
    discount_pct: DocValue = Field(default_factory=DocValue)
    net_price: DocValue = Field(default_factory=DocValue)
    delivery_date: DocValue = Field(default_factory=DocValue)


class Document(BaseModel):
    kind: Literal["po", "confirmation", "cart"]
    po_reference: DocValue = Field(default_factory=DocValue)
    version_hint: DocValue = Field(default_factory=DocValue)
    supplier: DocValue = Field(default_factory=DocValue)
    document_date: DocValue = Field(default_factory=DocValue)
    currency: DocValue = Field(default_factory=DocValue)
    incoterm: DocValue = Field(default_factory=DocValue)
    capture_date: DocValue = Field(default_factory=DocValue)
    lines: list[DocLine] = Field(default_factory=list)
    source_file: str = ""
    pages: int = 0
    reader: str = ""


# --- numbers, price basis, unit, currency ---

# The different spaces and apostrophes (no-break, thin, narrow, typographic) read as one space and one apostrophe.
_SPACES = str.maketrans({"\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u2019": "'"})

# One number in a text: digits, thousands marks (the same one each time: ".", ",", a space or an apostrophe,
# only before exactly three digits) and an optional decimal part. It must not be part of a longer run of
# digits and marks, so that "12.34.56" holds no number at all instead of "12.34".
_NUMBER_BODY = (
    r"(?P<int>[1-9][0-9]{0,2}(?P<group>[.,' ])[0-9]{3}(?:(?P=group)[0-9]{3})*|[0-9]+)"
    r"(?P<frac>[.,][0-9]+)?"
    r"(?![0-9]|[.,][0-9])")
_NUMBER = re.compile(r"(?<![0-9.,])" + _NUMBER_BODY)

# The words that put a price "per" something (English, Dutch, German). Each counts only as a whole word.
_PER_WORDS = ("per", "pro", "je", "p.", "à", "für", "voor", "for", "zu", "bei")
# Where a price basis stands: a number right after one of them or after a "/" ("per 100", "für 100 Stück", "/100 st") ...
_AFTER_PER = re.compile(r"(?:\b(?:" + "|".join(map(re.escape, _PER_WORDS)) + r")|/)\s*" + _NUMBER_BODY, re.IGNORECASE)
# ... or a number alone in the text, with at most one word (a unit) behind it ("100", "100 st"). It is matched
# against the stripped text, which keeps the pattern linear on long runs of spaces.
_ALONE = re.compile(_NUMBER_BODY + r"(?:\s*(?P<unit>[^\W\d_]+))?\.?")
_CURRENCY_MARK = re.compile(r"[€$£]|(?<![a-z])(?:eur|euros?|usd|gbp)(?![a-z])", re.IGNORECASE)
_MINUS_BEFORE = re.compile(r"(?<!\w)[-\u2212](?:[€$£]\s*)?$")     # "-12,50", "-€ 12,50", "€ -12,50", but not "KM-12"

_CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP"}

_UNITS = {
    "pc": ("st", "stk", "stuk", "stuks", "pcs", "pc", "piece", "pieces", "stück", "stueck"),
    "m": ("m", "mtr", "meter", "metre"),
    "kg": ("kg", "kilogram"),
    "l": ("l", "ltr", "liter", "litre"),
}
_UNIT_ALIASES = {alias: unit for unit, aliases in _UNITS.items() for alias in aliases}
_PER_ONE = set(_UNIT_ALIASES) | {"each", "ea", "unit", "einheit", "eenheid"}    # words that make a price "per one"


def normalize_number(text: str, decimal_comma: bool | None = None) -> str | None:
    """Return the number in text as a plain decimal string, or None when the text holds no number.

    "1.234,50" and "1,234.50" both give "1234.50", "12,5" gives "12.5", "€ 12,50" gives "12.50". The
    decimals stay as printed ("12.50" is not shortened to "12.5"). A currency sign or code and spaces
    around the number are ignored, a space or an apostrophe between groups of three digits is a thousands
    mark ("1 234,50", "1'234.50"), and a minus sign in front is kept ("-12,50" gives "-12.50"). When the
    text holds several numbers the first one is read: "€ 12,50 / 100 st" gives "12.50".

    Which mark is which:
    - A text with both marks takes the last one as the decimal mark.
    - A mark that repeats ("1.234.567", "1,234,567") is a thousands mark.
    - One "." or "," followed by exactly three digits and no other mark is ambiguous: "1.234" can be
      1234 or 1.234. It is read as a thousands mark only when the text also holds a currency sign or
      code ("€ 1.234" gives "1234"), and otherwise as a decimal mark ("1.234" gives "1.234").
    - Any other single "." or "," is a decimal mark ("12.5", "1234,5678", "0.123").
    A number that follows none of these rules ("1.234,56,7", "12.34.56") is not guessed at: None.

    decimal_comma is the convention of the document, which the reader knows from its layout or language.
    When it is given it settles the ambiguous case instead of the currency sign: True means "." is the
    thousands mark and "," the decimal mark ("1.000" gives "1000", "1,000" gives "1.000"), False the
    reverse ("1,000" gives "1000", "1.000" gives "1.000"). It changes nothing else. A mark that is not
    followed by exactly three digits is a decimal mark whatever the convention says, so a price with two
    decimals is the same under both values ("12,50" with False and "12.50" with True give "12.50"), and
    so is a text with both marks ("1.000,50" and "1,000.50" give "1000.50"). None, the default, leaves
    the case to the rule above.
    """
    text = text.translate(_SPACES)
    found = _NUMBER.search(text)
    if found is None:
        return None
    if decimal_comma is None:
        thousands_if_ambiguous = bool(_CURRENCY_MARK.search(text))
    else:
        thousands_if_ambiguous = found["group"] == ("." if decimal_comma else ",")
    value = _read_number(found, thousands_if_ambiguous)
    if value is not None and _MINUS_BEFORE.search(text[:found.start()]):
        value = "-" + value
    return value


def _read_number(found: re.Match[str], thousands_if_ambiguous: bool) -> str | None:
    """The plain decimal string of one match of a pattern built on _NUMBER_BODY, or None when its marks do not agree."""
    whole, mark, fraction = found["int"], found["group"], (found["frac"] or "")
    if mark is None:
        digits, decimals = whole, fraction[1:]
    else:
        if fraction[:1] == mark:                                  # "1.234.5": the decimal mark repeats the thousands mark
            return None
        thousands = mark in (" ", "'") or bool(fraction) or whole.count(mark) > 1 or thousands_if_ambiguous
        if thousands:
            digits, decimals = whole.replace(mark, ""), fraction[1:]
        else:                                                     # a single "." or ",": the decimal mark
            digits, decimals = whole.split(mark)
    return digits + ("." + decimals if decimals else "")


def normalize_basis(text: str) -> str | None:
    """Return the price basis in text: the number of units a price is for, as "1", "100", "1000".

    A number is the basis only where a basis stands: right after "per", "pro", "je", "p.", "à", "für",
    "voor", "for", "zu" or "bei", or the sign "/" ("per 100", "für 100 Stück", "/100 st", "€ 12,50 / 100 st"),
    or alone in the text with at most one word, a unit, behind it ("100", "100 st"). Of several such numbers
    the last one counts. Any other number is a price, a minimum or part of a unit and is left alone:
    "€ 13 / stuk", "pro Stück ab 5 St." and "per m2" give "1", "per 100 m2" gives "100". A unit may be glued
    to the basis ("per 100m").

    Any whole number above zero is kept as it is ("per 10" gives "10"), and a thousands mark is read as
    such ("per 1.000" gives "1000"), because a basis is a whole number. A basis that is not a whole number
    above zero ("per 2,5", "per 0") gives None. A text without such a number but with a word for one piece
    or unit ("per stuk", "each", "pro Stück", "per m") gives "1". Anything else gives None.
    """
    text = unicodedata.normalize("NFC", text).translate(_SPACES)
    candidates = list(_AFTER_PER.finditer(text))
    if not candidates:
        alone = _ALONE.fullmatch(text.strip())
        if alone and (alone["unit"] is None or alone["unit"].lower() in _PER_ONE):
            candidates = [alone]
    if candidates:
        basis = _read_number(candidates[-1], thousands_if_ambiguous=True)
        if basis is None or not basis.isdigit():
            return None                                           # "per 2,5": a basis that is not a whole number
        return basis.lstrip("0") or None                          # no leading zeros, and "0" is no basis
    words = re.findall(r"[^\W\d_]+", text.lower())
    return "1" if any(word in _PER_ONE for word in words) else None


def normalize_unit(text: str) -> str | None:
    """Return the unit of measure in text.

    "pc" for st, stk, stuk, stuks, pcs, pc, piece(s) and Stück (also written Stueck); "m" for m, mtr, meter
    and metre; "kg" for kg and kilogram; "l" for l, ltr, liter and litre. Any other unit gives its
    lower-cased text ("Doos" gives "doos"). Case, a final full stop and the spaces around the text do not
    matter. A text with nothing in it gives None.
    """
    cleaned = " ".join(unicodedata.normalize("NFC", text).lower().split()).strip(" .")
    return _UNIT_ALIASES.get(cleaned, cleaned) or None


def normalize_currency(text: str) -> str | None:
    """Return the ISO code of the currency in text: "€", "EUR" and "euro" give "EUR"; "$" and "USD"
    give "USD"; "£" and "GBP" give "GBP". Case and a final full stop do not matter. Any other text gives
    None, so that a currency the aid does not know is never taken for another one."""
    return _CURRENCIES.get(" ".join(text.lower().split()).strip(" ."))


# --- dates ---

_DAY_FIRST = re.compile(r"(?P<d>[0-9]{1,2})(?P<s>[-./])(?P<m>[0-9]{1,2})(?P=s)(?P<y>[0-9]{4})")
_YEAR_FIRST = re.compile(r"(?P<y>[0-9]{4})(?P<s>[-./])(?P<m>[0-9]{1,2})(?P=s)(?P<d>[0-9]{1,2})")
_ANY_DATE = r"[0-9]{1,4}[-./][0-9]{1,2}[-./][0-9]{1,4}"
_RANGE = re.compile(rf"(?P<start>{_ANY_DATE})\s*[-\u2013/]\s*(?P<end>{_ANY_DATE})")
_WEEK = re.compile(
    r"(?:(?P<year_before>[0-9]{4})\s*[-/ ]?\s*)?"
    r"(?:week|wk|kw|cw|w)[\s.:-]*(?P<week>[0-9]{1,2})"
    r"(?:\s*[-/ ]\s*(?P<year_after>[0-9]{4}))?", re.IGNORECASE)


def normalize_date(text: str, document_date: date | None) -> str | None:
    """Return the date in text as "2026-11-16", a week as "2026-W47", a range as "2026-11-16/2026-11-20".

    A date is day first with "-", "." or "/" ("16-11-2026", "16.11.2026") or year first ("2026-11-16"), with
    a four-digit year; a date that does not exist, or has a two-digit year, gives None. A range is two dates
    joined by a hyphen, an en dash or a slash, the first not after the second.

    A week is a number from 1 to 53 after "week", "wk", "KW", "CW" or "W" in any case, with or without a
    space, full stop or hyphen between ("week 47", "KW47"). A year can follow ("KW 47/2026") or lead
    ("2026-W47") and is then used as it stands. Without a year the week belongs to the year of
    document_date, or to the next year when the Monday of that week lies more than 182 days before
    document_date ("KW 1" on 28 December 2026 is "2027-W01"). A bare week number without a document_date
    gives None, because no year can be chosen. A week that does not exist in its year (week 53 of 2027)
    gives None.

    The normal forms themselves are read back unchanged, so a value can be normalised twice.
    """
    text = text.strip()
    single = _single_date(text)
    if single is not None:
        return single.isoformat()
    found = _RANGE.fullmatch(text)
    if found is not None:
        start, end = _single_date(found["start"]), _single_date(found["end"])
        return f"{start.isoformat()}/{end.isoformat()}" if start and end and start <= end else None
    return _normalize_week(text, document_date)


def _single_date(text: str) -> date | None:
    for pattern in (_DAY_FIRST, _YEAR_FIRST):
        found = pattern.fullmatch(text)
        if found is not None:
            try:
                return date(int(found["y"]), int(found["m"]), int(found["d"]))
            except ValueError:                                    # "31.02.2026"
                return None
    return None


def _monday(year: int, week: int) -> date | None:
    """The Monday of ISO week `week` of `year`, or None when that week does not exist."""
    try:
        return date.fromisocalendar(year, week, 1)
    except ValueError:
        return None


def _normalize_week(text: str, document_date: date | None) -> str | None:
    found = _WEEK.fullmatch(text)
    if found is None or (found["year_before"] and found["year_after"]):
        return None
    week = int(found["week"])
    given_year = found["year_before"] or found["year_after"]
    if given_year:
        year = int(given_year)
    elif document_date is None:
        return None
    else:
        year = document_date.year
        monday = _monday(year, week)
        if monday is not None and document_date.toordinal() - monday.toordinal() > 182:
            year += 1
    return f"{year:04d}-W{week:02d}" if _monday(year, week) is not None else None


# --- the schema for the AI ---

def document_schema() -> dict:
    """The JSON schema of Document for the AI's structured output: Document.model_json_schema() made
    strict. Every object has "additionalProperties": false and lists every property in "required", so a
    value that is not on the document is written as null and never left out. The defaults are removed,
    because with every property required they say nothing. A new dict is returned at each call."""
    schema = Document.model_json_schema()
    make_strict(schema)
    return schema


def make_strict(schema: dict) -> None:
    """Make one JSON schema and every schema inside it strict, in place. It follows the keywords
    pydantic writes (properties, $defs, anyOf, allOf, oneOf, items) and not the names of the properties,
    so that a field called "default" or "type" cannot be mistaken for a keyword."""
    schema.pop("default", None)
    properties = schema.get("properties", {})
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(properties)
    inner = [*properties.values(), *schema.get("$defs", {}).values(),
             *schema.get("anyOf", []), *schema.get("allOf", []), *schema.get("oneOf", [])]
    if isinstance(schema.get("items"), dict):
        inner.append(schema["items"])
    for child in inner:
        make_strict(child)
