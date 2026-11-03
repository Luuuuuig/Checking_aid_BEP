"""Settings of the checking aid: the thresholds, supplier lists, addresses and folders the buyer controls.

aid/settings.example.json shows every setting with its default. The real settings.json, with the
real addresses and the authorization limit, is kept outside the repository.

load_settings refuses a file that does not validate with a ValueError that names each field and its problem, never the value the
file holds, so that a mistyped authorization amount is not printed to a console or a service log.
"""
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class Settings(BaseModel):
    # A misspelled key must fail instead of being ignored: an ignored authorization limit or
    # threshold would silently change what the aid does on its own.
    model_config = ConfigDict(extra="forbid")

    language: Literal["nl", "en"] = "nl"
    price_pct: Decimal = Decimal("2.0")          # percent of the PO unit price
    price_abs: Decimal = Decimal("25.00")        # euro per line
    lead_time_tolerance_days: int = 7
    ask_wait_working_days: int = 3
    first_cases_review: int = 5
    # Reserved: no module reads it yet. The cart pre-check runs through the batch (python3 -m aid.batch, kind "cart"), which files
    # the ERP exports that give a cart its PO side; listing a supplier here has no effect.
    precheck_suppliers: list[str] = []
    review_suppliers: list[str] = []
    messages_off_suppliers: list[str] = []
    digest: Literal["daily", "per_case"] = "daily"
    digest_time: str = "16:30"
    authorization_limit: Decimal | None = None
    buyer_email: str
    buyer_name: str = ""                         # signs the messages under "Inkoop"; empty: "Inkoop" alone
    aid_email: str
    approver_email: str
    supplier_emails: dict[str, str] = {}         # supplier, as the PO names it -> the address the aid's questions go to
    po_number_pattern: str = r"(?i:PO|Inkooporder|Bestelling|Bestellung|Order)[\s:#-]*(\d{6,8})"
    cases_root: Path
    po_store_root: Path
    watched_folder: Path
    supplier_lead_times: dict[str, int] = {}     # supplier -> days, from the ERP export (question G7 of the solution requirements); unknown supplier -> None
    reader: Literal["llm", "parser"] = "llm"     # chosen by the AI-necessity check
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high"] = "medium"
    exact_base_url: str | None = None
    exact_endpoints: dict[str, str] = {}
    exact_fields: dict[str, str] = {}            # operation kind -> Exact field name, for the kinds whose field differs from the writer's default
    smtp_host: str | None = None                 # the aid's mailbox server; None files the messages instead of sending them
    smtp_port: int = 587
    smtp_user: str | None = None                 # the login of the aid's mailbox; its password is AID_MAIL_PASSWORD in the environment
    export_columns: dict[str, str] = {}
    review_url: str = "http://localhost:7860"


def load_settings(path: Path) -> Settings:
    """Read a settings file. Relative paths in it resolve against the folder that holds the file. A file that does not validate
    raises ValueError naming each field and its problem, without the values (pydantic's error, which quotes them, is left out)."""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))    # also reads a file saved with a BOM
    if isinstance(data, dict):                                 # anything else is left for validation to refuse
        for name, field in Settings.model_fields.items():
            if field.annotation is Path and isinstance(data.get(name), str):
                data[name] = str((path.parent / data[name]).resolve())
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc']) or 'the file'}: {error['msg']}"
                             for error in exc.errors(include_input=False, include_url=False))
        raise ValueError(f"settings file {path.name} is not valid: {problems}") from None
