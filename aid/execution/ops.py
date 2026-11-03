"""The write operations of the checking aid: one WriteOp is one change to the ERP, or one change prepared for the buyer.

old_value and new_value hold the values in the normal forms of the aid.schema module docstring: plain decimal strings
("12.50") for a price or a quantity, ISO dates ("2026-11-16") or weeks ("2026-W47") for a delivery date, and the status
text for a status. The routing decides which operations a finding gets, a writer applies them and the undo applies their
inverses (aid/execution/writer.py).
"""
from typing import Literal

from pydantic import BaseModel

# detach_document is the inverse of attach_document, used by the undo only.
OpKind = Literal["po_line_price", "po_line_date", "po_line_quantity", "article_price", "attach_document", "detach_document",
                 "set_status"]


class WriteOp(BaseModel):
    kind: OpKind
    po_number: str
    version: int | None
    line_id: str | None = None           # the PO line the operation concerns, where it concerns one
    article_code: str | None = None      # the article, where the operation concerns one
    old_value: str | None = None         # the value before the change
    new_value: str | None = None         # the value after it
    document: str | None = None          # the file of an attach_document or detach_document operation
