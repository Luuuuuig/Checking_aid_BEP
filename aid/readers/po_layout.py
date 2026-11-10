"""The layout of a purchase order PDF as the settings describe it: the setting po_layout (aid/settings.py), which the PO PDF reader
reads a PO with (read_po_layout of aid/readers/po_pdf.py).

The ERP e-mails each purchase order as a PDF. Its real layout is the company's, so it is not written into the code: the settings
file, which is kept on the company side, holds it as words that anyone can read off the page of a PO.

The header. A label is the text that stands before a value on the same line: po_label before the PO number, date_label before the
PO's date, supplier_label before the supplier, version_label before the version number (None: the PDF prints no version, and the
store numbers the versions in the order they arrive), currency_label before the currency and incoterm_label before the delivery
terms. A label may be several words ("Inkooporder nr."). A label left out, null or empty is a value the PDF does not print. A
supplier that stands alone on a line, as in an address block, is given by supplier_line, the number of that line on page 1 counted
from 1, in place of supplier_label.

The lines table. title_row holds the words of the row of column titles above the order lines, from left to right, exactly as the
page prints them, and columns holds the meaning of each of those words: a field of an order line (line_id, item_code, description,
quantity, unit, unit_price, price_basis, discount_pct, net_price, delivery_date), "same" for a word that belongs to the title on its
left (the second word of "Prijs per"), or "skip" for a column that is not read (a line amount). table_end, when given, is a text
that stands only on the row after the last order line (the whole label of the total row): it ends the table on its page, and a later
page is read only when it shows the title row again. decimal_comma is the number convention of the PDF: true for
"1.234,50", false for "1,234.50". prices_per_piece says that the PDF prints no price basis and that every price is per one unit:
the lines then still hold no price basis, and only the total of the PO counts each line that prints none as per one (aid/po_store.py).

A layout is refused, by the field at fault, when: po_label is empty; supplier_label and supplier_line are both given; title_row or
columns has fewer than two entries, or the two differ in length; an entry of title_row is empty or holds a space, since each printed
word is an entry of its own; the first column is "same"; no column is item_code; a field is the meaning of two columns ("same"
and "skip" may stand as often as needed); the columns hold neither line_id nor quantity, so that no row could be told to be an
order line. A key the layout does not know is refused too. The messages name the fields and never a word
of the layout, so that a refused settings file does not print the company's words into a log.

This module needs pydantic only, so that aid/settings.py imports it without the PDF libraries.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Meaning = Literal["line_id", "item_code", "description", "quantity", "unit", "unit_price", "price_basis", "discount_pct", "net_price",
                  "delivery_date", "same", "skip"]    # same: the word belongs to the title on its left; skip: a column that is not read
NOT_FIELDS = ("same", "skip")                     # the meanings that are no field of an order line


class PoLayout(BaseModel):
    """The layout of a PO PDF (see the module docstring)."""
    model_config = ConfigDict(extra="forbid")         # a misspelled key fails instead of being ignored

    po_label: str                                     # the text that stands before the PO number, on the same line
    date_label: str | None = None
    supplier_label: str | None = None                 # the text before the supplier; or
    supplier_line: int | None = Field(default=None, ge=1)     # the line of page 1 that holds the supplier alone, counted from 1
    version_label: str | None = None                  # None: the PDF prints no version
    currency_label: str | None = None
    incoterm_label: str | None = None
    title_row: list[str]                              # the words of the title row above the order lines, left to right
    columns: list[Meaning]                            # the meaning of each of those words
    table_end: str | None = None                      # a text on the first row after the last order line
    decimal_comma: bool = True
    prices_per_piece: bool = False                    # no price basis is printed and every price is per one unit: counts for the total only

    @field_validator("date_label", "supplier_label", "version_label", "currency_label", "incoterm_label", "table_end", mode="before")
    @classmethod
    def _empty_is_none(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("po_label")
    @classmethod
    def _a_label_for_the_number(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty: it is the text that stands before the PO number")
        return value.strip()

    @field_validator("supplier_line")
    @classmethod
    def _one_place_for_the_supplier(cls, value: int | None, info: ValidationInfo) -> int | None:
        if value is not None and info.data.get("supplier_label") is not None:
            raise ValueError("give supplier_label or supplier_line, not both")
        return value

    @field_validator("title_row")
    @classmethod
    def _a_row_of_titles(cls, value: list[str]) -> list[str]:
        if len(value) < 2:
            raise ValueError("needs at least two words")
        if any(not word or len(word.split()) != 1 or word != word.strip() for word in value):
            raise ValueError("each printed word is an entry of its own, so an entry is never empty and holds no space; the second word "
                             "of a title of two words is an entry of its own, whose column is 'same'")
        return value

    @field_validator("columns")
    @classmethod
    def _a_meaning_for_each_title(cls, value: list[str], info: ValidationInfo) -> list[str]:
        problems = []
        if len(value) < 2:
            problems.append("needs at least two entries")
        if "title_row" in info.data and len(value) != len(info.data["title_row"]):
            problems.append("must hold one meaning for each word of title_row")
        if value[:1] == ["same"]:
            problems.append("the first column cannot be 'same', which joins a word to the title on its left")
        if "item_code" not in value:
            problems.append("no column is item_code")
        fields = [meaning for meaning in value if meaning not in NOT_FIELDS]
        if len(set(fields)) != len(fields):
            problems.append("a field is the meaning of two columns")
        if "line_id" not in value and "quantity" not in value:
            problems.append("needs a line_id or a quantity column, by which a row is known to be an order line")
        if problems:
            raise ValueError("; ".join(problems))
        return value
