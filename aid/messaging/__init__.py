"""Supplier messages: the aid's standard questions to a supplier and its request to the approver, the mailers that send them, the
replies filed to the case and the wait for an answer (design specification, sections 5 and 6).

render gives the question of an ASK action (the template of its MessageSpec: quantity_unit, line_missing_extra or missing_info) as a
Message to the supplier's address with the buyer in copy; render_reapproval gives the request to the approver, the buyer in copy, to
approve a PO again after the aid changed it while it is above the buyer's authorization. The text is in the templates of
templates/nl/ and templates/en/, in the language of the settings: plain text that Jinja2 renders without escaping, and a variable a
template lacks fails the rendering. A question lists its items in a table (line, item code, description, our value, your value)
and asks for an answer by reply within the wait of the settings (ask_wait_working_days); the request lists the changes. Besides
that wait, a message shows no setting but the buyer's name and the addresses: never the authorization limit, nor a PO's total.

A mailer sends a Message and returns its SentRecord. FileMailer writes each message as a complete e-mail, <message id>.eml, and
sends nothing: for the made-up set and the demonstration. SmtpMailer sends through the aid's mailbox with STARTTLS, verifying the
server's certificate and name, with the password of an environment variable, read at each send and never kept. Whatever stops it is
a MailerError that names the class of the error, never the password, the login or a recipient. A To recipient the server refuses
fails the message as well; a refused Cc (the buyer's copy) does not, because the supplier has the question, and the record counts it
(refused_cc). Each e-mail has a Message-ID made of the message id and the sender's domain, which a reply's In-Reply-To names, the
header Auto-Submitted: auto-generated, and the template and the case id in the headers X-Aid-Template and X-Aid-Case.

The messages of a case are kept in <case root>/messages/:

    sent.jsonl       one SentRecord per message sent, a JSON line each (record_sent)
    replies/         the replies, copied there by file_reply
    bounces/         the bounces (is_bounce), copied there by file_reply
    auto/            the automatic replies (is_auto_reply), such as an out-of-office notice, copied there by file_reply
    escalated.jsonl  a JSON line {"case_id", "message_id", "reason", "at"} per question whose wait ended in review (record_escalation)

Filing. The watched folder files the answers: scan_folder (aid/sources/mailbox.py) calls file_answer of aid/pipeline.py for each
e-mail, which finds the case by the Message-ID that the e-mail's In-Reply-To or References header names (the message id of a question
in the case's sent.jsonl; a re-approval request is no question) and files it with file_reply. A filed reply puts the case's saved result in review, so that the buyer
reads it; a bounce ends the wait as below, and an automatic reply changes nothing. An e-mail that names no message of the aid is
not filed.

The wait. A case waits for an answer to its latest question; a reapproval is no question, and an automatic reply answers nothing.
The wait ends when a bounce is filed, or when no reply is and the deadline is past: the time the question was sent plus the working
days of the wait, Saturday and Sunday skipped, at the same time of day. ended_waits gives the questions whose wait has ended, each
once (a question escalated is not given again, a question sent after it waits on its own), and names apart the cases whose files
cannot be read, so that one damaged file stops no other case; pending_asks gives their case ids. The pipeline's escalate_pending
makes each a review item.
"""
import email
import functools
import os
import re
import shutil
import smtplib
import ssl
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, parseaddr
from pathlib import Path
from typing import NamedTuple, Protocol, get_args

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from aid.case import Case
from aid.execution.ops import WriteOp
from aid.routing import CaseContext, MessageSpec, Template
from aid.settings import Settings

TEMPLATES = Path(__file__).parent / "templates"  # one folder per language: nl/, en/
REAPPROVAL = "reapproval"                        # the template of the request to the approver to approve a PO again
QUESTIONS = get_args(Template)                   # the templates of the questions to a supplier (the ASK actions of the routing)
MESSAGES = "messages"                            # the folder of the messages in the case folder, and what it holds
SENT_FILE, ESCALATED_FILE, REPLIES, BOUNCES, AUTO = "sent.jsonl", "escalated.jsonl", "replies", "bounces", "auto"
BOUNCE = re.compile(r"mail delivery failed|undeliverable|delivery status notification|mailer-daemon"          # English
                    r"|niet bezorgd|kon niet worden bezorgd|onbestelbaar"                                     # Dutch
                    r"|unzustellbar|nicht zugestellt|zustellung fehlgeschlagen", re.IGNORECASE)               # German
AUTO_REPLY = re.compile(r"\s*(?:(?:re|fwd?|fw|aw|antw)\s*:\s*)*"                 # at the start, after reply and forward prefixes
                        r"(?:automatic reply|automatisch antwoord|automatische antwort|out of office|afwezig|abwesenheit)", re.IGNORECASE)
NO_DOMAIN = "aid.invalid"                        # the domain of a Message-ID whose sender has no address
SMTP_TIMEOUT = 30                                # seconds
_SUBJECTS = {"question": {"nl": "Vraag over orderbevestiging PO {po}", "en": "Question about order confirmation PO {po}"},
             REAPPROVAL: {"nl": "Inkooporder PO {po} opnieuw ter fiattering", "en": "PO {po} for approval again"}}


class Message(BaseModel):
    to: list[str]
    cc: list[str]
    subject: str
    body: str                                    # plain text
    template: str                                # the template the body was rendered from
    case_id: str                                 # the case the message belongs to


class SentRecord(BaseModel):
    message_id: str                              # the mailer's id of the message: a UUID in 32 hex digits
    to: list[str]
    subject: str
    sent_at: str                                 # when it was sent, in ISO form to the second
    template: str
    refused_cc: int = 0                          # the Cc addresses (the buyer's copy) the server refused; a refused To fails the send


class Mailer(Protocol):
    name: str

    def send(self, msg: Message) -> SentRecord: ...


class MailerError(Exception):
    """A message the mailer could not send. Its text names the class of the error behind it, never a password, a login or a recipient."""


class EndedWait(NamedTuple):
    """A question whose wait has ended (ended_waits)."""
    case_id: str
    message_id: str
    bounced: bool                                # it bounced; else it has no reply and its deadline is past


class _Escalation(BaseModel):
    """A line of escalated.jsonl."""
    case_id: str
    message_id: str
    reason: str
    at: str


# --- the messages ---

def render(spec: MessageSpec, case: Case, ctx: CaseContext, settings: Settings, supplier_email: str) -> Message:
    """The question of an ASK action to the supplier at supplier_email, the buyer in copy, in the language of the settings: the
    template of the spec, with its items in a table. An item gives its line_id (else its po_line_ids, joined by ", "), its
    item_code and description (blank when it has none), its po_value and its doc_value."""
    body = _templates(settings.language).get_template(f"{spec.template}.txt").render(
        po=ctx.po_number, items=[_row(item) for item in spec.items], wait_days=settings.ask_wait_working_days, buyer_name=settings.buyer_name)
    return Message(to=[supplier_email], cc=[settings.buyer_email], subject=_SUBJECTS["question"][settings.language].format(po=ctx.po_number),
                   body=body, template=spec.template, case_id=case.case_id)


def render_reapproval(ops: list[WriteOp], ctx: CaseContext, settings: Settings) -> Message:
    """The request to the approver, the buyer in copy, to approve the PO again after the aid made these changes to it, because the
    supplier's confirmation differs from the order while the PO is above the buyer's authorization. Each change is listed with its
    kind, its line or article and its old and new value. The context names no case, so the message's case_id is empty and the
    caller sets it."""
    body = _templates(settings.language).get_template(f"{REAPPROVAL}.txt").render(
        po=ctx.po_number, supplier=ctx.supplier, changes=ops, buyer_name=settings.buyer_name)
    return Message(to=[settings.approver_email], cc=[settings.buyer_email], body=body, template=REAPPROVAL, case_id="",
                   subject=_SUBJECTS[REAPPROVAL][settings.language].format(po=ctx.po_number))


@functools.cache
def _templates(language: str) -> Environment:
    """The templates of the language: plain text, nothing escaped, and a variable a template lacks fails the rendering."""
    environment = Environment(loader=FileSystemLoader(TEMPLATES / language), autoescape=False, undefined=StrictUndefined,
                              trim_blocks=True, lstrip_blocks=True)
    environment.globals["table"] = _table
    return environment


def _row(item: dict) -> dict[str, str]:
    """An item of a question as its table shows it (see render)."""
    lines = item.get("po_line_ids", "")
    return {"line_id": item.get("line_id", lines if isinstance(lines, str) else ", ".join(lines)), "item_code": item.get("item_code") or "",
            "description": item.get("description") or "", "po_value": item["po_value"], "doc_value": item["doc_value"]}


def _table(columns: list[tuple[str, str]], rows: list[dict]) -> str:
    """A plain-text table, for the templates: the labels of the columns, a rule, and a line per row with the value of each column's
    key. The columns are as wide as their widest cell and parted by " | "; an empty value shows as "-"."""
    cells = [[label for label, _ in columns]] + [[" ".join(str(row[key] or "").split()) or "-" for _, key in columns] for row in rows]
    widths = [max(len(line[n]) for line in cells) for n in range(len(columns))]
    lines = [" | ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in cells]
    return "\n".join([lines[0], "-+-".join("-" * width for width in widths), *lines[1:]])


# --- the mailers ---

class FileMailer:
    """Writes each message as a complete e-mail to <out_dir>/<message id>.eml and sends nothing: for the made-up set and the
    demonstration. The message id is a random UUID in hex and the time sent the clock's (datetime.now unless a clock is given)."""
    name = "file"

    def __init__(self, out_dir: Path, sender: str = "", clock: Callable[[], datetime] | None = None):
        self.out_dir, self.sender, self.clock = Path(out_dir), sender, clock or datetime.now

    def send(self, msg: Message) -> SentRecord:
        message_id, sent_at = uuid.uuid4().hex, self.clock()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{message_id}.eml").write_bytes(bytes(_email(msg, self.sender, sent_at, message_id)))
        return _sent(message_id, msg, sent_at)


class SmtpMailer:
    """Sends each message through an SMTP server with STARTTLS (the server's certificate and name verified), logged in as user, from
    the sender (the user when none is given). The password is read from the environment variable password_env at each send and never
    kept. A missing variable, a failed connection and an error of the server are a MailerError that names the class of the error and
    the step it stopped at, never the password or the login: the error's own text, which may quote the login, is left out, and so is
    the error as a cause. When the server refuses a To recipient, the MailerError counts the refused recipients and names none; a
    refused Cc alone is counted in the record."""
    name = "smtp"

    def __init__(self, host: str, port: int, user: str, password_env: str = "AID_MAIL_PASSWORD", sender: str | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.host, self.port, self.user, self.password_env = host, port, user, password_env
        self.sender, self.clock = sender or user, clock or datetime.now

    def send(self, msg: Message) -> SentRecord:
        try:
            password = os.environ[self.password_env]
        except KeyError:
            raise MailerError(f"KeyError: the environment variable {self.password_env} is not set") from None
        message_id, sent_at = uuid.uuid4().hex, self.clock()
        mail, step, answered, refused = _email(msg, self.sender, sent_at, message_id), "connect to", False, {}
        try:
            with smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT) as smtp:
                step = "start TLS with"
                smtp.starttls(context=ssl.create_default_context())
                step = "log in to"
                smtp.login(self.user, password)
                step = "send the message through"
                try:
                    refused = smtp.send_message(mail)            # the recipients refused while others were accepted
                except smtplib.SMTPRecipientsRefused as error:
                    if _refused(msg.to + msg.cc, error.recipients) < len(msg.to + msg.cc):
                        raise                                    # a refusal before every recipient was answered (421): nothing sent
                    refused = error.recipients                   # every recipient refused
                answered = True                                  # an error in closing the connection after this changes nothing
        except Exception as error:
            if not answered:
                code = getattr(error, "smtp_code", None)
                raise MailerError(f"{type(error).__name__}: could not {step} {self.host}:{self.port}"
                                  + (f" (SMTP code {code})" if code is not None else "")) from None
        if refused_to := _refused(msg.to, refused):
            raise MailerError(f"SMTPRecipientsRefused: {refused_to} of {len(msg.to)} To recipients refused")
        return _sent(message_id, msg, sent_at, refused_cc=_refused(msg.cc, refused))


def _email(msg: Message, sender: str, sent_at: datetime, message_id: str) -> EmailMessage:
    """The message as an e-mail: From (when there is a sender), To, Cc, Subject, Date (a time without a zone taken as local time),
    Message-ID (<message id@the sender's domain>, NO_DOMAIN for a sender without an address), Auto-Submitted, the template and the
    case in X-Aid-Template and X-Aid-Case, and the body as plain text in UTF-8."""
    address = parseaddr(sender)[1]
    mail = EmailMessage()
    if sender:
        mail["From"] = sender
    if msg.to:
        mail["To"] = ", ".join(msg.to)
    if msg.cc:
        mail["Cc"] = ", ".join(msg.cc)
    mail["Subject"] = msg.subject
    mail["Date"] = format_datetime(sent_at if sent_at.utcoffset() is not None else sent_at.astimezone())
    mail["Message-ID"] = f"<{message_id}@{(address.rpartition('@')[2] if '@' in address else '') or NO_DOMAIN}>"
    mail["Auto-Submitted"] = "auto-generated"
    mail["X-Aid-Template"] = msg.template
    mail["X-Aid-Case"] = msg.case_id
    mail.set_content(msg.body, charset="utf-8")
    return mail


def _refused(addresses: list[str], refused: dict) -> int:
    """How many of the addresses are among the recipients the server refused, compared in lower case (refused maps each refused
    address to the server's answer, which is not kept)."""
    refused_addresses = {address.lower() for address in refused}
    return sum(parseaddr(address)[1].lower() in refused_addresses for address in addresses)


def _sent(message_id: str, msg: Message, sent_at: datetime, refused_cc: int = 0) -> SentRecord:
    return SentRecord(message_id=message_id, to=msg.to, subject=msg.subject, sent_at=sent_at.isoformat(timespec="seconds"), template=msg.template,
                      refused_cc=refused_cc)


# --- the case folder: the messages sent, the replies and the wait ---

def record_sent(case: Case, rec: SentRecord) -> None:
    """Append the record of a message sent to <case root>/messages/sent.jsonl, creating the folder and the file on first use."""
    _append_line(case.root / MESSAGES / SENT_FILE, rec.model_dump_json())


def file_reply(case: Case, eml: Path) -> Path:
    """Copy an e-mail that answers the case to messages/replies/, a bounce (is_bounce) to messages/bounces/ and an automatic reply
    (is_auto_reply) to messages/auto/, and return the path of the copy. A name the folder holds already gets _2, _3 and so on before
    its extension, as in the watched folder."""
    eml = Path(eml)
    folder = case.root / MESSAGES / (BOUNCES if is_bounce(eml) else AUTO if is_auto_reply(eml) else REPLIES)
    folder.mkdir(parents=True, exist_ok=True)
    target, number = folder / eml.name, 2
    while target.exists():
        target, number = folder / f"{eml.stem}_{number}{eml.suffix}", number + 1
    shutil.copy2(eml, target)
    return target


def is_bounce(eml: Path) -> bool:
    """Whether the e-mail is a bounce: its subject or its sender matches BOUNCE (the English, Dutch and German forms, such as "mail
    delivery failed", "onbestelbaar", "unzustellbar" or "mailer-daemon"), in upper or lower case."""
    subject, sender, _ = _headers(eml)
    return _bounce(subject, sender)


def is_auto_reply(eml: Path) -> bool:
    """Whether the e-mail is an automatic reply and no bounce: its subject begins with the words of AUTO_REPLY ("automatic reply",
    "automatisch antwoord", "automatische Antwort", "out of office", "afwezig", "Abwesenheit"), in upper or lower case, after any
    reply or forward prefixes ("Re:", "Fwd:", "FW:", "AW:", "Antw:"), so that a reply that only mentions them is a reply; or it has
    an Auto-Submitted header other than "no". It is neither a reply nor a bounce, and it ends no wait."""
    subject, sender, auto_submitted = _headers(eml)
    automatic = bool(AUTO_REPLY.match(subject)) or auto_submitted.partition(";")[0].strip().lower() not in ("", "no")
    return automatic and not _bounce(subject, sender)


def bounced(case: Case) -> bool:
    """Whether a bounce is filed to the case (messages/bounces/)."""
    return _holds_files(case.root / MESSAGES / BOUNCES)


def ended_waits(cases_root: Path, now: datetime, working_days: int) -> tuple[list[EndedWait], list[tuple[str, str]]]:
    """The questions whose wait has ended at now, and the cases whose files cannot be read as (case id, class of the error), both in
    the order of the case folders. A folder under cases_root that holds messages/sent.jsonl is a case that sent messages, and its
    wait concerns its latest question (a message of an ASK template; a reapproval never counts). The wait has ended when a file is in
    messages/bounces/, or when messages/replies/ holds none and now is past the question's deadline (_deadline). A question
    escalated once (record_escalation) is not given again, while a question sent after it waits on its own. escalated.jsonl is read
    first, and sent.jsonl from its end back to the latest question, so that the messages before it are never read again. A file
    that cannot be read or parsed (OSError, or ValueError, which a JSON error is) puts its case among the unreadable, and the others
    go on."""
    ended, unreadable = [], []
    for path in sorted(Path(cases_root).glob(f"*/{MESSAGES}/{SENT_FILE}")):
        folder, case_id = path.parent, path.parent.parent.name
        try:
            escalated = _escalated(folder / ESCALATED_FILE)
            question = _latest_question(path)
            if question is None or question.message_id in escalated:
                continue
            bounced_now = _holds_files(folder / BOUNCES)
            ended_now = bounced_now or (not _holds_files(folder / REPLIES)
                                        and _past(now, _deadline(datetime.fromisoformat(question.sent_at), working_days)))
        except (OSError, ValueError) as error:
            unreadable.append((case_id, type(error).__name__))
            continue
        if ended_now:
            ended.append(EndedWait(case_id, question.message_id, bounced_now))
    return ended, unreadable


def pending_asks(cases_root: Path, now: datetime, working_days: int) -> list[str]:
    """The ids of the cases whose question's wait has ended at now (ended_waits), in the order of their folders; a case whose files
    cannot be read is left out, and ended_waits names it."""
    return [wait.case_id for wait in ended_waits(cases_root, now, working_days)[0]]


def record_escalation(case: Case, reason: str, at: datetime, message_id: str | None = None) -> None:
    """Note in messages/escalated.jsonl that the wait for a question ended in review, and why: ended_waits does not give that question
    again. The question is message_id, else the case's latest question."""
    if message_id is None:
        sent = case.root / MESSAGES / SENT_FILE
        latest = _latest_question(sent) if sent.exists() else None
        message_id = latest.message_id if latest is not None else ""
    _append_line(case.root / MESSAGES / ESCALATED_FILE,
                 _Escalation(case_id=case.case_id, message_id=message_id, reason=reason, at=at.isoformat()).model_dump_json())


def _deadline(sent_at: datetime, working_days: int) -> datetime:
    """The end of the wait: sent_at plus the working days, Saturday and Sunday skipped (no holidays), at the same time of day. Sent on
    Friday 20 November at 16:00 with 3 working days, the deadline is Wednesday 25 November at 16:00."""
    deadline, left = sent_at, working_days
    while left > 0:
        deadline += timedelta(days=1)
        if deadline.weekday() < 5:              # Monday to Friday
            left -= 1
    return deadline


def _past(now: datetime, deadline: datetime) -> bool:
    """Whether now is past the deadline. A time without a zone is local time, so that one with a zone and one without compare."""
    if (now.utcoffset() is None) != (deadline.utcoffset() is None):
        now, deadline = now.astimezone(), deadline.astimezone()
    return now > deadline


def _latest_question(path: Path) -> SentRecord | None:
    """The latest question of a sent.jsonl, read from the end of the file back to it; None when it holds no question. A damaged line
    on the way raises ValueError (a pydantic ValidationError)."""
    for line in reversed(path.read_bytes().split(b"\n")):
        if line.strip():
            record = SentRecord.model_validate_json(line)
            if record.template in QUESTIONS:
                return record
    return None


def _escalated(path: Path) -> set[str]:
    """The ids of the questions escalated (escalated.jsonl); none when the file does not exist. A damaged line raises ValueError."""
    if not path.exists():
        return set()
    return {_Escalation.model_validate_json(line).message_id for line in path.read_bytes().split(b"\n") if line.strip()}


def _headers(eml: Path) -> tuple[str, str, str]:
    """The Subject, the From and the Auto-Submitted header of an e-mail file, decoded; "" for a header it lacks."""
    with open(eml, "rb") as f:
        mail = email.message_from_binary_file(f, policy=policy.default)
    subject, sender, auto_submitted = (str(mail[name] or "") for name in ("Subject", "From", "Auto-Submitted"))
    return subject, sender, auto_submitted


def _bounce(subject: str, sender: str) -> bool:
    return bool(BOUNCE.search(subject) or BOUNCE.search(sender))


def _holds_files(folder: Path) -> bool:
    return folder.is_dir() and any(path.is_file() for path in folder.iterdir())


def _append_line(path: Path, line: str) -> None:
    """Add the line at the end of the file, creating the file and its folder on first use. When the file does not end with a newline
    (a crash cut its last line off), the line starts a line of its own, as in the write log."""
    data = line.encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as f:
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)                            # the file is opened for appending, so this goes to its end
