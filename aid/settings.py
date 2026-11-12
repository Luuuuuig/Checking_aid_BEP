"""Settings of the checking aid: the thresholds, supplier lists, addresses and folders the buyer controls.

aid/settings.example.json shows every setting with its default. The real settings.json, with the
real addresses and the authorization limit, is kept outside the repository.

load_settings refuses a file that does not validate with a ValueError that names each field and its problem, never the value the
file holds, so that a mistyped authorization amount is not printed to a console or a service log; read_settings reads the same
way and raises SettingsInvalid, which also lists the fields, for the settings tab of the review page, which names them by their
labels. update_settings_file writes values into a settings file, as that tab does. It holds one lock of the process from reading
the file to the rename, so that two saves pressed at the same moment both land, and it can build its values from the settings the
file holds at that moment. It writes only the keys whose value differs from the file's: the text of such a value is replaced
where it stands, a key the file lacks is added at its end, and every other character of the file is kept, so that every other
key keeps its bytes, the order of the keys, the file's indentation, its line ends and a byte order mark stay, and a save that
changes nothing leaves the file and its modification time alone. On Linux and macOS the file keeps its permissions (on Windows
the new file takes the access its folder gives, so the settings file belongs in a folder that only the runner's user can read).
The file is validated first, values that do not validate are refused with SettingsInvalid, and the file is replaced through a
temporary file with a name of its own beside it and a rename, so that a runner that reads it meanwhile reads either the old file
or the new one; a write that fails removes the temporary file and leaves the file as it was.

The six settings of the work mailbox (ms_tenant_id, ms_client_id, mailbox, mail_folder, mail_folder_name, send_via) are optional:
with them empty or null the aid works as before, from the watched folder. An empty text counts as null. The tenant and the client
are the IDs of the app registration in Microsoft Entra ID, which are identifiers and no secrets: the client a GUID, the tenant a GUID
or its domain name. send_via "microsoft" needs both.

po_layout is the layout of the PO PDF that the ERP e-mails, as words read off its page (PoLayout of aid/readers/po_layout.py): with
it the aid reads every PO PDF with that layout, and takes a PDF that fits it for a PO. It is validated with the settings, and a
layout at fault is named by its field (po_layout.title_row), never by a word it holds. po_senders are the senders of the PO e-mails,
one address or one domain (the part after the at sign) to each entry; an entry that is neither is refused by its place
(po_senders.1), never by what it holds. Fill it whenever po_layout is set: without it a supplier's document that repeats the PO's
page is filed as a PO. With both empty or null the aid works as before, with the made-up layout.

article_columns and article_decimal_comma belong to the price list on the review page (aid/pricelist.py,
aid/review/pricelist_page.py): the columns and the number convention of the export of a supplier's articles from Exact, set in part 3
of the block "Koppeling met Exact" of the settings tab. With article_columns empty the tab "Prijslijst" offers nothing. The columns of
each supplier's price list, which that tab keeps when a list is carried out, are data and no setting: they are kept beside the stored
article prices, under <cases_root>/exact, so that a press of the tab never changes the settings file.

exact_base_url stays empty. No command builds a write route into Exact (aid/execution/exact_api.py says what that module is), so a
settings file in which the key is set is refused wherever a file is read or written here, with the one sentence WRITE_ROUTE_SET
(WriteRouteSet, a SettingsInvalid that names the field): the runner, the review page, the capture server and the launcher end with
it at their start, and a part that is running keeps the settings in use. The model keeps the three keys of a route
(exact_base_url, exact_endpoints, exact_fields), so that an older file with the key null or empty still loads and the tests of the
writer can build settings with them in code.

test_mode says that the aid works with the test account of Exact, for the owner's test before the hand-over (false by default). While
it is true, whatever the authorization limit, the tab "In Exact invoeren" of the review page says that its changes go into the test
Exact only, and only the switch on the settings tab ends it: setting a limit does not. While it is true and no authorization limit is
set, the settings tab offers a try-out confirmation made from a purchase order the aid knows (aid/tryout.py), and only then does the
scan of the watched folder take a try-out (aid/tryout_mark.py). The switch on the settings tab writes it; the first start of the folder
version writes it true, and the hand-over switches it off on the settings tab as a step of its own.
"""
import codecs
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator

from aid.readers.po_layout import PoLayout

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DOMAIN = re.compile(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
_SENDER = re.compile(r"[^@\s,;<>()\[\]\"']+@" + _DOMAIN.pattern)       # the address of a sender: no list, no name, nothing around it
_WRITING = threading.Lock()                      # one save of a settings file at a time in this process (update_settings_file)
_BLANK = " \t\n\r"                               # the white space of JSON
WRITE_ROUTE_SET = ('exact_base_url is set, but this version of the aid writes nothing in Exact: every change is prepared for the tab '
                   '"In Exact invoeren". Remove exact_base_url from the settings file.')


def _sender(entry: str) -> str:
    """An entry of po_senders without its outer spaces: an e-mail address, or a domain with or without the at sign before it."""
    entry = entry.strip()
    if not (_SENDER.fullmatch(entry) or _DOMAIN.fullmatch(entry.removeprefix("@"))):
        raise ValueError("must be one e-mail address or one domain (the part after the at sign, with or without the at sign): no name "
                         "around it, no second address, no comma")
    return entry


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
    # The export of PO lines that gives a cart pre-check its PO side (aid/readers/po_export.py): the aid's field name -> the column
    # title of the file. It must name po_number and item_code and may name line_id, description, quantity, unit, unit_price,
    # price_basis, discount_pct, net_price, delivery_date and supplier, the last only for a column that holds the supplier as the
    # supplier lists above name it. Empty: each column is named after its field.
    export_columns: dict[str, str] = {}
    export_decimal_comma: bool = False           # numbers the export writes as text use the decimal comma: 1.234,50
    export_fill_down: bool = False               # the export prints the PO number once for each PO, on its first line or above it
    # The export of a supplier's articles with their purchase prices, which the price list on the review page is compared with
    # (aid/pricelist.py): the aid's field name -> the column title of the file. It names item_code and price, and supplier only for an
    # export that names the supplier of each row. Empty: the tab "Prijslijst" offers nothing yet.
    article_columns: dict[str, str] = {}
    article_decimal_comma: bool = False          # numbers that export writes as text use the decimal comma: 1.234,50
    # The PO PDF that the ERP e-mails (aid/readers/po_pdf.py, aid/sources/mailbox.py). None: only the made-up layout is read.
    po_layout: PoLayout | None = None            # the layout of that PDF; with it a PDF that fits is a PO PDF, read with the layout alone
    # Who sends those e-mails: addresses, or domains as the part after the at sign. Not empty: a PDF that fits the layout and came
    # in an e-mail from anyone else is a confirmation. Empty: any sender.
    po_senders: list[Annotated[str, AfterValidator(_sender)]] = []
    review_url: str = "http://localhost:7860"
    # The token the browser extension sends to the capture server (aid/capture_server.py) in the header X-Aid-Token. It is
    # company-specific: set it in the real settings.json only, never in a repository. None (or empty) keeps the server from starting.
    capture_token: str | None = None
    # The test mode: the aid works with the test account of Exact. While it is on, whatever the authorization limit, the tab "In Exact
    # invoeren" says to enter the changes in the test Exact only, and only the switch on the settings tab ends it. While it is on and no
    # authorization limit is set, the review page offers a try-out confirmation made from a stored PO (aid/tryout.py) and the watched
    # folder takes one (aid/tryout_mark.py). Off in the example; the first settings file of the folder version turns it on, and the
    # hand-over switches it off.
    test_mode: bool = False

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


class WriteRouteSet(SettingsInvalid):
    """A settings file in which exact_base_url is set: refused with the one sentence WRITE_ROUTE_SET, since no command builds a
    write route into Exact (see the module docstring)."""

    def __init__(self):
        super().__init__(WRITE_ROUTE_SET, ["exact_base_url"])


def is_tenant_id(text: str) -> bool:
    """Whether the text is a tenant as ms_tenant_id takes one: a GUID, or a domain name such as contoso.onmicrosoft.com."""
    return bool(_GUID.fullmatch(text) or _DOMAIN.fullmatch(text))


def is_address(text: str) -> bool:
    """Whether the text is an e-mail address by the rule the settings check one with (mailbox): one @, no spaces, a dot after it."""
    return bool(_ADDRESS.fullmatch(text))


def is_client_id(text: str) -> bool:
    """Whether the text is a client as ms_client_id takes one: a GUID."""
    return bool(_GUID.fullmatch(text))


def load_settings(path: Path) -> Settings:
    """Read a settings file. Relative paths in it resolve against the folder that holds the file. A file that does not validate
    raises ValueError naming each field and its problem, without the values (pydantic's error, which quotes them, is left out);
    a file in which exact_base_url is set raises WriteRouteSet, a ValueError too, whose text is the one sentence."""
    try:
        return read_settings(path)
    except WriteRouteSet:
        raise
    except SettingsInvalid as error:
        raise ValueError(str(error)) from None


def read_settings(path: Path) -> Settings:
    """Read a settings file as load_settings does, for a caller that names the fields at fault itself: content that does not
    validate raises SettingsInvalid, with the text of load_settings' error and the fields in `fields` ("the file" for content that
    is no JSON object). A file that cannot be opened raises OSError, and one that is no JSON another ValueError."""
    path = Path(path).resolve()
    return _validated(json.loads(path.read_bytes().decode("utf-8-sig")), path)        # also reads a file saved with a BOM


def update_settings_file(path: Path, values: Mapping[str, object] | Callable[[Settings], Mapping[str, object]]) -> Settings:
    """Write values into the settings file at path and return the settings it then holds. `values` is a mapping, or a function
    that is given the settings the file holds and returns the mapping: the function is called inside the lock that this function
    holds from reading the file to the rename, so that values built from the file (a list with one name more) never lose what
    another save wrote a moment before; a file that does not validate as it is raises SettingsInvalid before the function is called.

    Only the keys whose value differs from the file's are written (compared as JSON, so 7 differs from "7" and from 7.0): the text
    of such a value is replaced where it stands, a list or an object in the file's indentation, and a key the file lacks is added
    at its end, in the order of the mapping. Every other character of the file is kept: every other key keeps its bytes (also a
    number written as 5000.00 or a letter written as an escape), and so do the order of the keys, the indentation (a file on one
    line stays on one line), the line ends and a byte order mark. When no value differs, nothing is written and the file keeps its
    modification time. The new content is validated as load_settings reads it before it is written to a temporary file with a
    name of its own beside the file (.<name>.<random>.tmp), which is then renamed over it. On Linux and macOS the file keeps its
    permissions: the temporary file is made for its owner only and given the file's mode before the rename, so that a file only
    its owner may read is never readable by others there. On Windows a mode sets no more than the read-only flag, and the new
    file takes the access its folder gives (see the module docstring). Content that does not validate raises SettingsInvalid as
    load_settings does, naming the fields and never the values, and the file is left as it was. A write that fails leaves the
    file as it was too: its temporary file is removed and the error is raised."""
    path = Path(path).resolve()
    with _WRITING:
        content = path.read_bytes()
        raw = content.decode("utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise SettingsInvalid(f"settings file {path.name} is not valid: it holds no JSON object", [])
        if callable(values):
            values = values(_validated(data, path))
        changed = {key: value for key, value in values.items() if key not in data or _json(data[key]) != _json(value)}
        data.update(changed)
        settings = _validated(data, path)
        if not changed:
            return settings
        text = _spliced(raw, changed)
        if _json(json.loads(text)) != _json(data):               # never: the file would not hold what was validated
            raise ValueError(f"settings file {path.name}: the changed text does not hold the changed values")
        mark = codecs.BOM_UTF8 if content.startswith(codecs.BOM_UTF8) else b""
        mode = stat.S_IMODE(path.stat().st_mode)
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)       # for its owner only
        try:
            with os.fdopen(handle, "wb") as f:
                f.write(mark + text.encode("utf-8"))
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
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
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        fields = list(dict.fromkeys(".".join(str(part) for part in error["loc"]) or "the file" for error in errors))
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc']) or 'the file'}: {error['msg']}" for error in errors)
        raise SettingsInvalid(f"settings file {path.name} is not valid: {problems}", fields) from None
    if settings.exact_base_url:                                # no command builds a write route into Exact (see the module docstring)
        raise WriteRouteSet()
    return settings


def _indent(raw: str) -> int | str | None:
    """The indentation of a JSON file as json.dumps takes it: that of its first key, or None for a file on one line."""
    for line in raw.splitlines()[1:]:
        if (stripped := line.lstrip()).startswith('"'):
            lead = line[:len(line) - len(stripped)]
            return lead if "\t" in lead else len(lead)
    return None


def _json(value) -> str:
    """A value as JSON text, by which two values are compared as a settings file holds them: 7, "7" and 7.0 differ, and so do two
    objects whose keys stand in another order."""
    return json.dumps(value, ensure_ascii=False)


def _spliced(raw: str, changed: Mapping[str, object]) -> str:
    """The text of a JSON object with the value of each key of `changed` replaced where it stands, and each key the object lacks
    added after its last member; every other character of raw is kept. A value is written as json.dumps writes it, a list or an
    object over several lines in the indentation and with the line ends of the file, on one line in a file on one line."""
    indent = _indent(raw)
    lead = "" if indent is None else indent if isinstance(indent, str) else " " * indent
    newline = "\r\n" if "\r\n" in raw else "\n"

    def written(value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=indent).replace("\n", newline + lead)

    spans, last, close = _members(raw)
    pieces = [(*spans[key], written(value)) for key, value in changed.items() if key in spans]
    added = [f"{json.dumps(key, ensure_ascii=False)}: {written(value)}" for key, value in changed.items() if key not in spans]
    if added:
        between = f",{newline}{lead}" if indent is not None else ", "
        at = last if last is not None else close            # after the last member, or inside an object without members
        pieces.append((at, at, (between if last is not None else "") + between.join(added)))
    for start, end, text in sorted(pieces, reverse=True):    # from the end of the file, so that the places before it stay
        raw = raw[:start] + text + raw[end:]
    return raw


def _members(raw: str) -> tuple[dict[str, tuple[int, int]], int | None, int]:
    """Where the values of a JSON object stand in its text: the first and the past-the-last index of the value of each key (of
    the last one, for a key given twice, as json.loads reads it), the end of the last value (None for an object without members)
    and the index of the closing brace. The text is one that json.loads read as an object."""
    decoder = json.JSONDecoder()

    def skip(index: int) -> int:
        while raw[index] in _BLANK:
            index += 1
        return index

    spans, last = {}, None
    index = skip(skip(0) + 1)                                # past the opening brace
    while raw[index] != "}":
        key, index = decoder.raw_decode(raw, index)
        start = skip(skip(index) + 1)                        # past the colon
        _, last = decoder.raw_decode(raw, start)
        spans[key] = (start, last)
        index = skip(last)
        if raw[index] == ",":
            index = skip(index + 1)
    return spans, last, index
