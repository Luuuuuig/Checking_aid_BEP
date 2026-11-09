"""Settings of the checking aid: the thresholds, supplier lists, addresses and folders the buyer controls.

aid/settings.example.json shows every setting with its default. The real settings.json, with the
real addresses and the authorization limit, is kept outside the repository.

load_settings refuses a file that does not validate with a ValueError that names each field and its problem, never the value the
file holds, so that a mistyped authorization amount is not printed to a console or a service log. update_settings_file writes
values into a settings file, as the settings tab of the review page does, keeping every other key and the order of the keys, the
file's indentation and, on Linux and macOS, its permissions (on Windows the new file takes the access its folder gives, so the
settings file belongs in a folder that only the runner's user can read); the file is validated first, values that do not
validate are refused with SettingsInvalid, which also lists the fields, and the file is replaced through a temporary file and a
rename, so that a runner that reads it meanwhile reads either the old file or the new one.

The six settings of the work mailbox (ms_tenant_id, ms_client_id, mailbox, mail_folder, mail_folder_name, send_via) are optional:
with them empty or null the aid works as before, from the watched folder. An empty text counts as null. The tenant and the client
are the IDs of the app registration in Microsoft Entra ID, which are identifiers and no secrets: the client a GUID, the tenant a GUID
or its domain name. send_via "microsoft" needs both.
"""
import json
import os
import re
import stat
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DOMAIN = re.compile(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")


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
    # the ERP exports that give a cart its PO side, or through the capture server (aid/capture_server.py); listing a supplier here
    # has no effect.
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
    # The work mailbox through a Microsoft sign-in (aid/connections/, aid/sources/microsoft_mail.py, aid/messaging/microsoft_mailer.py).
    ms_tenant_id: str | None = None              # the Directory (tenant) ID of the app registration: a GUID, or the tenant's domain name
    ms_client_id: str | None = None              # the Application (client) ID of the app registration: a GUID
    mailbox: str | None = None                   # the address of the mailbox read and sent from; None: the signed-in account's own
    mail_folder: str | None = None               # the id of its folder that the runner reads, as Graph gives it; None: no mailbox is read
    mail_folder_name: str | None = None          # the folder's display name, shown on the settings tab
    # auto: microsoft when signed in and mail_folder is set, else smtp when the SMTP settings and the password are there, else file
    send_via: Literal["auto", "file", "smtp", "microsoft"] = "auto"
    export_columns: dict[str, str] = {}
    review_url: str = "http://localhost:7860"
    # The token the browser extension sends to the capture server (aid/capture_server.py) in the header X-Aid-Token. It is
    # company-specific: set it in the real settings.json only, never in a repository. None (or empty) keeps the server from starting.
    capture_token: str | None = None

    @field_validator("ms_tenant_id", "ms_client_id", "mailbox", "mail_folder", "mail_folder_name", mode="before")
    @classmethod
    def _empty_is_null(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("ms_tenant_id")
    @classmethod
    def _tenant(cls, value: str | None) -> str | None:
        if value is not None and not is_tenant_id(value):
            raise ValueError("must be the Directory (tenant) ID, a GUID, or the tenant's domain name")
        return value

    @field_validator("ms_client_id")
    @classmethod
    def _client(cls, value: str | None) -> str | None:
        if value is not None and not is_client_id(value):
            raise ValueError("must be the Application (client) ID, a GUID")
        return value

    @field_validator("mailbox")
    @classmethod
    def _mailbox(cls, value: str | None) -> str | None:
        if value is not None and not _ADDRESS.fullmatch(value):
            raise ValueError("must be an e-mail address")
        return value

    @field_validator("send_via")
    @classmethod
    def _microsoft_needs_the_ids(cls, value: str, info: ValidationInfo) -> str:
        if value == "microsoft" and not (info.data.get("ms_tenant_id") and info.data.get("ms_client_id")):
            raise ValueError("'microsoft' needs ms_tenant_id and ms_client_id")
        return value


class SettingsInvalid(ValueError):
    """Settings that do not validate: the text names each field and its problem, and `fields` lists the fields; never a value."""

    def __init__(self, text: str, fields: list[str]):
        super().__init__(text)
        self.fields = fields


def is_tenant_id(text: str) -> bool:
    """Whether the text is a tenant as ms_tenant_id takes one: a GUID, or a domain name such as contoso.onmicrosoft.com."""
    return bool(_GUID.fullmatch(text) or _DOMAIN.fullmatch(text))


def is_client_id(text: str) -> bool:
    """Whether the text is a client as ms_client_id takes one: a GUID."""
    return bool(_GUID.fullmatch(text))


def load_settings(path: Path) -> Settings:
    """Read a settings file. Relative paths in it resolve against the folder that holds the file. A file that does not validate
    raises ValueError naming each field and its problem, without the values (pydantic's error, which quotes them, is left out)."""
    path = Path(path).resolve()
    try:
        return _validated(json.loads(path.read_text(encoding="utf-8-sig")), path)     # also reads a file saved with a BOM
    except SettingsInvalid as error:
        raise ValueError(str(error)) from None


def update_settings_file(path: Path, values: Mapping[str, object]) -> Settings:
    """Write the values into the settings file at path and return the settings it then holds. Every other key keeps its value and
    its place, a key already in the file keeps its place, and a key the file lacks is added at its end. The new content is validated
    as load_settings reads it before it is written to .<name>.tmp beside the file, which is then renamed over it. The file keeps its
    indentation (a file on one line stays on one line) and, on Linux and macOS, its permissions: the temporary file is made with
    them, so that a file only its owner may read is never readable by others there. On Windows a mode sets no more than the
    read-only flag, and the new file takes the access its folder gives (see the module docstring). JSON is written again from the
    values, so a number keeps its value, not
    always its text ("2.50" in a file becomes 2.5; the amounts of the settings are texts). Content that does not validate raises
    SettingsInvalid as load_settings does, naming the fields and never the values, and the file is left as it was."""
    path = Path(path).resolve()
    raw = path.read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SettingsInvalid(f"settings file {path.name} is not valid: it holds no JSON object", [])
    data.update(values)
    settings = _validated(data, path)
    mode, temporary = stat.S_IMODE(path.stat().st_mode), path.with_name(f".{path.name}.tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=_indent(raw), ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return settings


def _validated(data, path: Path) -> Settings:
    """The settings of the content of the file at path (see load_settings); the content itself is not changed."""
    if isinstance(data, dict):                                 # anything else is left for validation to refuse
        data = dict(data)
        for name, field in Settings.model_fields.items():
            if field.annotation is Path and isinstance(data.get(name), str):
                data[name] = str((path.parent / data[name]).resolve())
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        fields = list(dict.fromkeys(".".join(str(part) for part in error["loc"]) or "the file" for error in errors))
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc']) or 'the file'}: {error['msg']}" for error in errors)
        raise SettingsInvalid(f"settings file {path.name} is not valid: {problems}", fields) from None


def _indent(raw: str) -> int | str | None:
    """The indentation of a JSON file as json.dumps takes it: that of its first key, or None for a file on one line."""
    for line in raw.splitlines()[1:]:
        if (stripped := line.lstrip()).startswith('"'):
            lead = line[:len(line) - len(stripped)]
            return lead if "\t" in lead else len(lead)
    return None
