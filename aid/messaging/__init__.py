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
sends nothing: for the made-up set and the demonstration, and for a run in which nothing may leave (send_via "file"); one given
for want of a way to send carries what is missing (its `unrouted`). SmtpMailer sends through the aid's mailbox with STARTTLS,
verifying the server's certificate and name, with the password of an environment variable, read at each send and never kept.
Whatever stops it is a MailerError that names the class of the error, never the password, the login or a recipient, and carries
the kind of its cause. A To recipient the server refuses fails the message before it is given, so that it goes to nobody; a refused
Cc (the buyer's copy) does not, because the supplier has the question, and the record counts it (refused_cc). A failure while the
message is handed over, or before the confirmation of it, is HandOverUnconfirmed, which carries the record of the message: nobody
knows whether it left, and the caller counts it as sent. Each e-mail has a Message-ID made of the message id and the sender's
domain, which a reply's In-Reply-To names, the header Auto-Submitted: auto-generated, and the template and the case id in the
headers X-Aid-Template and X-Aid-Case. MicrosoftMailer (microsoft_mailer.py) sends through the work mailbox with the user's
Microsoft sign-in, and its record keeps the Message-ID that Exchange gave the message (internet_message_id), whose part before
the "@" is then the record's message id.

The messages of a case are kept in <case root>/messages/:

    sent.jsonl       one SentRecord per message sent, a JSON line each (record_sent)
    replies/         the replies, copied there by file_reply
    bounces/         the bounces, copied there by file_reply
    auto/            the automatic messages, such as an out-of-office notice or a mail server's notice of a delay
    own/             the messages of the aid's own side, such as the buyer's own reply to all
    escalated.jsonl  a JSON line {"case_id", "message_id", "reason", "at"} per question whose wait ended in review (record_escalation)

Filing. The watched folder files the answers: scan_folder (aid/sources/mailbox.py) calls file_answer of aid/pipeline.py for each
e-mail, which finds the case by the message ids the e-mail names (named_ids: the Message-ID that its In-Reply-To or References header
names, and for a delivery report in the standard form the Message-ID of the message it returns; the message id of a question in the
case's sent.jsonl; a re-approval request is no question) and files it with file_reply, in the folder of its kind (kind_of). A filed
reply puts the case's saved result in review, so that the buyer reads it; a bounce ends the wait of its question as below; an
automatic message and a message of the aid's own side change nothing. An e-mail that names no message of the aid is not filed.

A reply belongs to its question. A filed reply answers one question of its case: the first of the ids it names that is a question
of the case, which is the one its In-Reply-To names, else the last one its References names; a bounce belongs to its question in
the same way, and a delivery report to the question whose message it returns. An e-mail that names no message at all (one put into
the folder by hand) belongs to the latest question sent before it was filed, and one that names only another message belongs to
none. So a supplier who answers two of the aid's e-mails in one reply answers the one that reply names, and the other comes back to
the buyer when its wait ends. A question that has a reply of its own is answered, whatever bounce names it besides. A filed e-mail
that cannot be read at that moment belongs to no question and is taken for no answer: its case cannot be judged until it reads.
open_questions gives the questions of a case that wait for an answer, bounced_questions those that bounced and were not shown to
the buyer yet, and outstanding_questions both.

The wait. A case waits while one of its questions has no reply of its own; a reapproval is no question, and an automatic message
answers nothing. A bounce ends the wait of its own question at once. Without one, the wait that counts is that of the latest
question without a reply: it ends when the deadline of that question is past, the time the question was sent plus the working days
of the wait, Saturday and Sunday skipped, at the same time of day. An escalation (record_escalation) covers the question it names
and the questions sent before it whose own wait had ended by then: a later question's bounce ends no wait that still runs.
ended_waits gives the questions whose wait has ended, each once (a question an escalation covers is not given again; any other
waits on its own), and names apart the cases whose files cannot be read, so that one damaged file stops no other case; pending_asks
gives their case ids. The pipeline's escalate_pending makes each a review item, and its end_empty_waits brings a case back that
waits while no question of it is out.
"""
import email
import functools
import json
import os
import re
import shutil
import smtplib
import ssl
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.utils import format_datetime, getaddresses, parseaddr
from pathlib import Path
from typing import NamedTuple, Protocol, get_args

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from aid.audit import AUDIT_FILE
from aid.case import Case
from aid.execution.ops import WriteOp
from aid.routing import CaseContext, MessageSpec, Template
from aid.settings import Settings

TEMPLATES = Path(__file__).parent / "templates"  # one folder per language: nl/, en/
REAPPROVAL = "reapproval"                        # the template of the request to the approver to approve a PO again
QUESTIONS = get_args(Template)                   # the templates of the questions to a supplier (the ASK actions of the routing)
MESSAGES = "messages"                            # the folder of the messages in the case folder, and what it holds
SENT_FILE, ESCALATED_FILE, REPLIES, BOUNCES, AUTO, OWN = "sent.jsonl", "escalated.jsonl", "replies", "bounces", "auto", "own"
_PREFIX = r"(?:re|fwd?|fw|aw|antw|wg|tr|doorst)\s*:"                            # a reply or forward prefix, as mail programs write it
_PREFIXES = rf"\s*(?:{_PREFIX}\s*)*"                                           # the prefixes a subject begins with
# What may stand before the words of a bounce: the prefixes, tags in square brackets ("[EXTERN]") and tags between asterisks
# ("***SPAM***"), as a mail gateway that marks outside mail puts them there, and one word in capitals with a colon ("EXTERN:").
_TAGS = rf"\s*(?:(?:\[[^\]]*\]|\*+[^*]+\*+|{_PREFIX})\s*)*"
_LEAD = rf"{_TAGS}(?:(?-i:[A-Z]{{2,}}):\s*)?{_TAGS}"
_NOTICE = r"delivery status notification\s*\(\s*(?:delay|success)"             # a notice of a delay or a success: no bounce (kind_of)
_BOUNCE_WORDS = (r"mail delivery failed|message undeliverable|undeliverable|undelivered mail|returned mail|failure notice"   # English
                 r"|delivery has failed|delivery status notification|mailer-daemon"
                 r"|niet bezorgd|k[oa]n niet worden bezorgd|onbestelbaar|niet bestelbaar|niet te bezorgen"                  # Dutch
                 r"|unzustellbar|nicht zustellbar|nicht zugestellt|zustellung fehlgeschlagen")                              # German
BOUNCE = re.compile(_BOUNCE_WORDS, re.IGNORECASE)                                 # anywhere in the sender
# In the subject the words count at its start, after the lead; a Dutch or a German notice may name the message first ("Uw bericht
# kon niet worden bezorgd", "Nachricht wurde nicht zugestellt").
BOUNCE_SUBJECT = re.compile(_LEAD + r"(?:(?:uw |het )?bericht (?:is |werd )?|(?:ihre |die )?nachricht (?:wurde |konnte )?)?"
                            rf"(?:{_BOUNCE_WORDS})", re.IGNORECASE)
DELIVERY_NOTICE = re.compile(_LEAD + _NOTICE, re.IGNORECASE)                      # no bounce, whatever its sender: an automatic message
NO_FAILURE = ("delayed", "delivered", "relayed", "expanded")                      # the Actions of a delivery report that is no bounce
AUTO_REPLY = re.compile(_PREFIXES +                                               # at the start, after reply and forward prefixes
                        r"(?:automatic reply|automatisch antwoord|automatische antwort|out of office|afwezig|abwesenheit)", re.IGNORECASE)
MESSAGE_ID = re.compile(r"<([^<>@\s]+)@[^<>\s]*>")    # a Message-ID, "<local part@domain>", whose local part is the aid's message id
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
    message_id: str                              # the mailer's id of the message, the part of its Message-ID before the "@": a UUID in
                                                 # 32 hex digits, or that part of the id Exchange gave it (internet_message_id)
    to: list[str]
    subject: str
    sent_at: str                                 # when it was sent, in ISO form to the second
    template: str
    refused_cc: int = 0                          # the Cc addresses (the buyer's copy) the server refused; a refused To fails the send
    # The whole Message-ID as Exchange gives it (MicrosoftMailer); None for the other mailers, and then left out of a record's JSON.
    internet_message_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    # True for a message whose hand-over was not confirmed (HandOverUnconfirmed): it counts as sent, so that a reply or a bounce
    # that names it belongs to it; left out of a record's JSON otherwise.
    unconfirmed: bool = Field(default=False, exclude_if=lambda value: not value)


class Mailer(Protocol):
    name: str

    def send(self, msg: Message) -> SentRecord: ...


class MailerError(Exception):
    """A message the mailer could not send. Its text names the class of the error behind it, never a password, a login or a
    recipient. `kind` is the kind of its cause, by which the review page says what to do (aid/review/page.py): "sign_in" (the
    Microsoft sign-in has lapsed), "unreachable" (no connection, a time-out, an answer that another try may get past), "refused"
    (the mailbox or the server will not take the message), "no_password" (the mail server's password is not set), or what a
    mailer without a route gives (NoRoute)."""

    def __init__(self, text: str, kind: str = "refused"):
        super().__init__(text)
        self.kind = kind


class HandOverUnconfirmed(MailerError):
    """A message of which nobody knows whether it left: the failure came while it was handed over, or before the confirmation of
    it (SMTP: after the data was given; the work mailbox: at the send of the draft). `record` is the record of the message, with
    its id and marked `unconfirmed`. The caller records it as sent, so that a reply or a bounce that names it finds its case and
    belongs to its question, and sends nothing again: a second copy would carry another id."""

    def __init__(self, text: str, record: SentRecord):
        super().__init__(text, "unconfirmed")
        self.record = record


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
    supplier's confirmation differs from the order while the PO is above the buyer's authorization; for a case of the kind cart
    (ctx.kind), which has no confirmation yet, because the cart at the supplier differs from the order. Each change is listed with
    its kind, its line or article and its old and new value. The context names no case, so the message's case_id is empty and the
    caller sets it."""
    body = _templates(settings.language).get_template(f"{REAPPROVAL}.txt").render(
        po=ctx.po_number, supplier=ctx.supplier, changes=ops, buyer_name=settings.buyer_name, cart=ctx.kind == "cart")
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
    demonstration. The message id is a random UUID in hex and the time sent the clock's (datetime.now unless a clock is given).
    `unrouted` is None for a mailer that writes files by choice (send_via "file", a mailer made in code), and says why for one
    given for want of a way to send (mailer_of of microsoft_mailer.py: "no_mailbox" or "no_password"). The mailer writes its file
    either way; the review page's close neither writes nor counts a message through a mailer that has no route
    (aid/review/close.py)."""
    name = "file"

    def __init__(self, out_dir: Path, sender: str = "", clock: Callable[[], datetime] | None = None, unrouted: str | None = None):
        self.out_dir, self.sender, self.clock, self.unrouted = Path(out_dir), sender, clock or datetime.now, unrouted

    def send(self, msg: Message) -> SentRecord:
        message_id, sent_at = uuid.uuid4().hex, self.clock()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{message_id}.eml").write_bytes(bytes(_email(msg, self.sender, sent_at, message_id)))
        return _sent(message_id, msg, sent_at)


class NoRoute:
    """The mailer of a moment at which the way to send cannot be told (`unrouted` says why: "settings" when the settings file
    cannot be read): it sends nothing and writes nothing, and what is given to it is a message not sent."""
    name = "none"

    def __init__(self, unrouted: str):
        self.unrouted = unrouted

    def send(self, msg: Message) -> SentRecord:
        raise MailerError(f"no way to send: {self.unrouted}", self.unrouted)


class SmtpMailer:
    """Sends each message through an SMTP server with STARTTLS (the server's certificate and name verified), logged in as user, from
    the sender (the user when none is given). The password is read from the environment variable password_env at each send and never
    kept. A missing variable, a failed connection and an error of the server are a MailerError that names the class of the error and
    the step it stopped at, never the password or the login: the error's own text, which may quote the login, is left out, and so is
    the error as a cause.

    The message is given by hand (MAIL, RCPT for each recipient, DATA), so that the mailer can stop before the message is given:
    when the server refuses a To recipient, the message goes to nobody, the copy included, and the MailerError counts the refused
    recipients and names none; a refused Cc alone is counted in the record, and the message goes to the others. Until the message
    is given a failure is a message not sent. A failure after that, while the message is given or before the server's answer to
    it, is HandOverUnconfirmed: nobody knows whether the message left. An answer of the server that refuses the message it was
    given is a message not sent again."""
    name = "smtp"

    def __init__(self, host: str, port: int, user: str, password_env: str = "AID_MAIL_PASSWORD", sender: str | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.host, self.port, self.user, self.password_env = host, port, user, password_env
        self.sender, self.clock = sender or user, clock or datetime.now

    def send(self, msg: Message) -> SentRecord:
        try:
            password = os.environ[self.password_env]
        except KeyError:
            raise MailerError(f"KeyError: the environment variable {self.password_env} is not set", "no_password") from None
        message_id, sent_at = uuid.uuid4().hex, self.clock()
        mail, step, given, answered, refused = _email(msg, self.sender, sent_at, message_id), "connect to", False, False, {}
        try:
            with smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT) as smtp:
                step = "start TLS with"
                smtp.starttls(context=ssl.create_default_context())
                step = "log in to"
                smtp.login(self.user, password)
                step = "send the message through"
                data, refused = self._offer(smtp, mail, msg)
                if not _refused(msg.to, refused):
                    code, reply = smtp.docmd("data")
                    if code != 354:
                        raise smtplib.SMTPDataError(code, reply)
                    given = True                             # from here on a failure leaves it unknown whether the message left
                    smtp.send(data)
                    code, reply = smtp.getreply()
                    given = False                            # the server answered: it took the message, or it says that it did not
                    if code != 250:
                        raise smtplib.SMTPDataError(code, reply)
                answered = True                              # an error in closing the connection after this changes nothing
        except Exception as error:
            if given:
                raise HandOverUnconfirmed(f"{type(error).__name__}: no confirmation that {self.host}:{self.port} took the message",
                                          _sent(message_id, msg, sent_at).model_copy(update={"unconfirmed": True})) from None
            if not answered:
                code = getattr(error, "smtp_code", None)
                raise MailerError(f"{type(error).__name__}: could not {step} {self.host}:{self.port}"
                                  + (f" (SMTP code {code})" if code is not None else ""), _smtp_kind(error)) from None
        if refused_to := _refused(msg.to, refused):
            raise MailerError(f"SMTPRecipientsRefused: {refused_to} of {len(msg.to)} To recipients refused",
                              _smtp_kind(smtplib.SMTPRecipientsRefused(refused)))
        return _sent(message_id, msg, sent_at, refused_cc=_refused(msg.cc, refused))

    def _offer(self, smtp: smtplib.SMTP, mail: EmailMessage, msg: Message) -> tuple[bytes, dict]:
        """Name the sender and each recipient of the message to the server, and return the message as it is given after DATA (with
        CRLF line ends, a line that begins with a period given a second one, and the line that ends it) and the recipients the
        server refused, each with its answer. With a To recipient among them the conversation is reset, so that nothing of the
        message stays with the server. A sender the server refuses, and an answer 421 to a recipient (the server closes), raise
        as smtplib's own sendmail raises them."""
        smtp.ehlo_or_helo_if_needed()
        sender = getaddresses([str(mail["From"] or "")])[0][1]
        recipients = [address for _, address in getaddresses([*msg.to, *msg.cc])]
        options, shape = [], mail.policy
        if not "".join([sender, *recipients]).isascii():     # an address beyond ASCII needs the server's SMTPUTF8, as smtplib asks
            if not smtp.has_extn("smtputf8"):
                raise smtplib.SMTPNotSupportedError("the server does not take the addresses of this message")
            options, shape = ["SMTPUTF8", "BODY=8BITMIME"], mail.policy.clone(utf8=True)
        data = re.sub(rb"(?m)^\.", b"..", mail.as_bytes(policy=shape.clone(linesep="\r\n")))
        data += (b"" if data.endswith(b"\r\n") else b"\r\n") + b".\r\n"
        if smtp.does_esmtp and smtp.has_extn("size"):
            options = [f"size={len(data)}", *options]
        code, reply = smtp.mail(sender, options)
        if code != 250:
            smtp.close() if code == 421 else _reset(smtp)
            raise smtplib.SMTPSenderRefused(code, reply, sender)
        refused: dict[str, tuple] = {}
        for recipient in recipients:
            code, reply = smtp.rcpt(recipient)
            if code not in (250, 251):
                refused[recipient] = (code, reply)
            if code == 421:
                smtp.close()
                raise smtplib.SMTPRecipientsRefused(refused)
        if _refused(msg.to, refused):
            _reset(smtp)
        return data, refused


def _reset(smtp: smtplib.SMTP) -> None:
    """Reset the conversation, so that the server forgets the sender and the recipients named; a connection lost on the way
    changes nothing, since nothing was given."""
    try:
        smtp.docmd("rset")
    except smtplib.SMTPServerDisconnected:
        pass


def _smtp_kind(error: Exception) -> str:
    """The kind of the cause of a message the SMTP mailer could not send (MailerError): "unreachable" for what another try may get
    past (no connection, a time-out, a connection lost, an answer of the server with a 4xx code), "refused" for anything else (a
    5xx code, a login the server does not accept, a certificate that does not verify)."""
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [answer[0] for answer in error.recipients.values()]
        return "unreachable" if codes and all(400 <= code < 500 for code in codes) else "refused"
    if isinstance(error, smtplib.SMTPResponseException):
        return "unreachable" if 400 <= error.smtp_code < 500 else "refused"
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return "unreachable"
    return "unreachable" if isinstance(error, OSError) and not isinstance(error, (smtplib.SMTPException, ssl.SSLError)) else "refused"


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


def file_reply(case: Case, eml: Path, own: Iterable[str | None] = ()) -> Path:
    """Copy an e-mail that answers the case to the folder of its kind (kind_of, with the addresses of the aid's own side): a reply
    to messages/replies/, a bounce to messages/bounces/, an automatic message to messages/auto/ and a message of the aid's own side
    to messages/own/, and return the path of the copy. A name the folder holds already gets _2, _3 and so on before its extension,
    as in the watched folder."""
    eml = Path(eml)
    folder = case.root / MESSAGES / _FOLDERS[kind_of(eml, own)]
    folder.mkdir(parents=True, exist_ok=True)
    target, number = folder / eml.name, 2
    while target.exists():
        target, number = folder / f"{eml.stem}_{number}{eml.suffix}", number + 1
    shutil.copy2(eml, target)
    return target


_FOLDERS = {"reply": REPLIES, "bounce": BOUNCES, "auto": AUTO, "own": OWN}


def kind_of(eml: Path, own: Iterable[str | None] = ()) -> str:
    """What an e-mail that answers a case is: "bounce", "auto" (an automatic message), "own" (a message of the aid's own side) or
    "reply", in this order:

    - A delivery report in the standard form (multipart/report with the report type delivery-status, RFC 3464) says itself what it
      is, whatever its subject or sender: the Action fields of its delivery-status part make it a bounce when one of them is
      "failed", and an automatic message when each is "delayed", "delivered", "relayed" or "expanded" (NO_FAILURE). A report whose
      Action cannot be read is judged as any other e-mail.
    - An e-mail whose sender (the address of its From) is one of `own`, the addresses of the aid's own side (the buyer's, the aid's
      and that of the mailbox; compared without regard to capitals, None and "" passed over), is a message of that side, whatever
      its subject: the buyer is in copy of every question, so his own reply to all names the question.
    - A bounce: its subject begins with the words of a bounce (BOUNCE_SUBJECT: the English, Dutch and German forms, such as "mail
      delivery failed", "onbestelbaar", "unzustellbar" or "mailer-daemon"), in upper or lower case, after what mail programs and
      gateways put before them (reply and forward prefixes, tags in square brackets or between asterisks, one word in capitals with
      a colon), so that a reply that only mentions them further on is a reply; or its sender holds one of them (BOUNCE), as a mail
      system's own address does. A notice of a delay or a success by its subject ("Delivery Status Notification (Delay)",
      DELIVERY_NOTICE) is no bounce, whatever its sender, but an automatic message.
    - An automatic message: its subject begins with the words of AUTO_REPLY ("automatic reply", "automatisch antwoord",
      "automatische Antwort", "out of office", "afwezig", "Abwesenheit"), in upper or lower case, after any reply or forward
      prefixes ("Re:", "Fwd:", "FW:", "AW:", "Antw:", "WG:", "TR:", "Doorst:"), so that a reply that only mentions them is a reply;
      or it has an Auto-Submitted header other than "no". It ends no wait and answers nothing.
    - Any other e-mail is a reply, whoever sent it: a supplier may answer from another address, and the buyer reads the reply."""
    with open(eml, "rb") as f:
        mail = email.message_from_binary_file(f, policy=policy.default)
    subject, sender, auto_submitted = (str(mail[name] or "") for name in ("Subject", "From", "Auto-Submitted"))
    if (reported := _reported(mail)) is not None:
        return reported
    if parseaddr(sender)[1].lower() in {address.strip().lower() for address in own if address and address.strip()}:
        return "own"
    notice = bool(DELIVERY_NOTICE.match(subject))
    if not notice and _bounce(subject, sender):
        return "bounce"
    automatic = notice or bool(AUTO_REPLY.match(subject)) or auto_submitted.partition(";")[0].strip().lower() not in ("", "no")
    return "auto" if automatic else "reply"


def is_bounce(eml: Path) -> bool:
    """Whether the e-mail is a bounce (kind_of, without the addresses of the aid's own side)."""
    return kind_of(eml) == "bounce"


def is_auto_reply(eml: Path) -> bool:
    """Whether the e-mail is an automatic message and no bounce (kind_of, without the addresses of the aid's own side): it is
    neither a reply nor a bounce, and it ends no wait."""
    return kind_of(eml) == "auto"


def bounced(case: Case) -> bool:
    """Whether a bounce is filed to the case (messages/bounces/)."""
    return _holds_files(case.root / MESSAGES / BOUNCES)


def ended_waits(cases_root: Path, now: datetime, working_days: int,
                waiting: Callable[[str], bool] | None = None) -> tuple[list[EndedWait], list[tuple[str, str]]]:
    """The questions whose wait has ended at now and the cases whose files cannot be read as (case id, class of the error), both in
    the order of the case folders, and the questions of a case in the order they were sent. A folder under cases_root that holds
    messages/sent.jsonl is a case that sent messages, and its wait concerns its questions that no escalation covers (a message of an
    ASK template; a reapproval never counts), each with the replies and the bounces that belong to it (_wait). The wait has ended
    for each of them that a bounce belongs to and no reply, at once; and for the latest of them that neither a reply nor a bounce
    belongs to, when now is past its deadline (_deadline): with two questions without a reply the wait runs from the latest one. A
    question escalated once (record_escalation) is not given again, nor is an earlier one whose own wait had ended by then, while
    any other question waits on its own. A file that cannot be read or parsed (OSError, or ValueError, which a JSON error is), a
    filed reply or bounce that cannot be read at that moment among them, puts its case among the unreadable, and the others go on.

    `waiting`, when given, is asked about a case (by its id) before anything of the case is read, and a case it denies is passed
    over: so the pass of escalate_pending reads the filed replies of the cases that wait, and of no other."""
    ended, unreadable = [], []
    for path in sorted(Path(cases_root).glob(f"*/{MESSAGES}/{SENT_FILE}")):
        folder, case_id = path.parent, path.parent.parent.name
        if waiting is not None and not waiting(case_id):
            continue
        try:
            wait = _wait(folder, working_days)
            silent, returned = wait.open(), {question.message_id for question in wait.bounced()}
            latest = silent[-1].message_id if silent and _past(now, _deadline(datetime.fromisoformat(silent[-1].sent_at), working_days)) else None
            found = [EndedWait(case_id, question.message_id, question.message_id in returned) for question in wait.questions
                     if question.message_id in returned or question.message_id == latest]
        except (OSError, ValueError) as error:
            unreadable.append((case_id, type(error).__name__))
            continue
        ended += found
    return ended, unreadable


def open_questions(folder: Path, working_days: int) -> list[SentRecord]:
    """The questions of a case's messages/ folder that wait for an answer, in the order they were sent: those of sent.jsonl that no
    escalation covers, with the wait of `working_days`, and to which no reply and no bounce belongs (_wait). A file that cannot be
    read or parsed raises OSError or ValueError; a filed reply or bounce that cannot be read at that moment raises OSError, since
    nobody can say then which question it answers."""
    return _wait(folder, working_days).open()


def bounced_questions(folder: Path, working_days: int) -> list[SentRecord]:
    """The questions of a case's messages/ folder that bounced and were not shown to the buyer yet, in the order they were sent:
    those no escalation covers to which a bounce belongs and no reply (_wait). The wait of such a question has ended (ended_waits),
    and until its escalation it has no answer, so a close does not confirm the order over it (aid/review/close.py). A question that
    has a reply of its own is answered, whatever bounce is filed of it besides."""
    return _wait(folder, working_days).bounced()


def outstanding_questions(folder: Path, working_days: int) -> list[SentRecord]:
    """The questions of a case's messages/ folder that are still out, in the order they were sent: those that wait for an answer
    (open_questions) and those that bounced and were not shown to the buyer yet (bounced_questions). While it holds one the case
    waits for something; a file that cannot be read raises as for open_questions."""
    wait = _wait(folder, working_days)
    out = {question.message_id for question in (*wait.open(), *wait.bounced())}
    return [question for question in wait.questions if question.message_id in out]


def named_ids(eml: Path) -> list[str]:
    """The message ids that an e-mail file names (the part of each Message-ID before its "@", as a SentRecord keeps it), in the order
    in which they count: those of its In-Reply-To header, then those of its References header from the last back to the first, and
    for a delivery report in the standard form (multipart/report with the report type delivery-status, RFC 3464), which a receiving
    mail server sends without either header, the Message-ID of the message it returns: of the part of the report itself that holds
    the returned headers (text/rfc822-headers) or the returned message (message/rfc822), and of a returned message only its own top
    headers, never those of a message attached inside it. The first of them that is a question of a case is the question the
    e-mail belongs to. An id counts only in angle brackets, "<id@domain>". Any other e-mail that carries a message, a forward or a
    read receipt, names by its own headers alone. Only those headers are read, up to the first empty line, unless the e-mail is a
    delivery report: a reply with a large attachment costs no more than its headers."""
    with open(eml, "rb") as f:
        head = BytesHeaderParser(policy=policy.default).parsebytes(_header_block(f))
        named = MESSAGE_ID.findall(" ".join(str(value) for value in head.get_all("In-Reply-To") or []))
        named += reversed(MESSAGE_ID.findall(" ".join(str(value) for value in head.get_all("References") or [])))
        if _is_report(head):
            f.seek(0)
            named += _returned_ids(email.message_from_binary_file(f, policy=policy.default))
    return list(dict.fromkeys(named))


def _header_block(f) -> bytes:
    """The headers of an e-mail file opened for reading bytes: what stands before its first empty line."""
    lines = []
    for line in f:
        if not line.strip():
            break
        lines.append(line)
    return b"".join(lines)


def _is_report(mail) -> bool:
    """Whether the e-mail, or its headers, is a delivery report in the standard form."""
    return mail.get_content_type() == "multipart/report" and str(mail.get_param("report-type") or "").lower() == "delivery-status"


def _returned_ids(report) -> list[str]:
    """The message ids of the Message-ID headers of the messages that a delivery report returns, whole or by their headers alone:
    of the report's own parts only, and of a returned message only its own headers."""
    found = []
    for part in report.iter_parts():
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            returned = payload[0] if isinstance(payload, list) and payload else None
        elif part.get_content_type() == "text/rfc822-headers":
            returned = BytesHeaderParser(policy=policy.default).parsebytes(part.get_payload(decode=True) or b"")
        else:
            continue
        if returned is not None:
            found += MESSAGE_ID.findall(" ".join(str(value) for value in returned.get_all("Message-ID") or []))
    return found


def _reported(mail) -> str | None:
    """What a delivery report in the standard form says it is (kind_of): "bounce" when an Action field of its delivery-status part
    is "failed", "auto" when each of them is one of NO_FAILURE; None for an e-mail that is no such report, and for a report without
    an Action that can be read or with one that is not known."""
    if not _is_report(mail):
        return None
    actions = [action for part in mail.iter_parts() if part.get_content_type() == "message/delivery-status" for action in _actions(part)]
    if "failed" in actions:
        return "bounce"
    return "auto" if actions and all(action in NO_FAILURE for action in actions) else None


def _actions(part) -> list[str]:
    """The values of the Action fields of a delivery-status part, in lower case: the part is a row of blocks of fields, one for the
    message and one for each recipient, which the parser gives as messages."""
    blocks = part.get_payload()
    if not isinstance(blocks, list):
        return [action.strip().lower() for action in re.findall(r"(?im)^action:([^\r\n]*)", str(blocks))]
    return [str(value).strip().lower() for block in blocks if hasattr(block, "get_all") for value in block.get_all("Action") or []]


class _Wait(NamedTuple):
    """The questions of a messages/ folder and where each stands (_wait)."""
    questions: list[SentRecord]                  # in the order sent
    settled: set[str]                            # the ids of those an escalation covers
    answered: set[str]                           # the ids of those a reply belongs to
    returned: set[str]                           # the ids of those a bounce belongs to and no reply

    def open(self) -> list[SentRecord]:
        return [question for question in self.questions if question.message_id not in self.settled | self.answered | self.returned]

    def bounced(self) -> list[SentRecord]:
        return [question for question in self.questions if question.message_id in self.returned - self.settled]


def _wait(folder: Path, working_days: int) -> _Wait:
    """The questions of a messages/ folder, in the order sent (_sent_questions), with the ids of those an escalation covers, of those
    a reply belongs to and of those a bounce belongs to and no reply: a question that has a reply of its own is answered, whatever
    bounce names it besides.

    An escalation (a line of escalated.jsonl) covers the question it names and the questions sent before it whose own wait, of
    `working_days`, had ended at the time of the escalation: a later question's bounce ends no wait that still runs.

    A reply (a file of replies/) belongs to the first of the ids it names (named_ids) that is a question of the folder: the question
    its In-Reply-To names, else the last one its References names, else the one a delivery report returns. When an escalation
    covers that question, the reply ends no wait, also when it names a later question further on. A file that names no message at
    all (one put there by hand) belongs to the latest question sent before it was filed, by the time of its entry in the list
    "answers" of the case's audit record, else by the file's own time, and never to a question sent after it (a question sent
    within the same second as the filing counts as sent after it, since the times are kept to the second); one that names only
    other messages belongs to none. A bounce (a file of bounces/) belongs to its question in the same way; an automatic message
    (auto/) and a message of the aid's own side (own/) belong to none. The replies and the bounces are read only for a case that
    has a question no escalation covers. A filed e-mail that cannot be read at that moment raises OSError, so that it is never
    taken for an answer nor for none; one that cannot be read as an e-mail at all names nothing and belongs to none."""
    questions = _sent_questions(folder / SENT_FILE)
    settled = _settled(questions, _escalations(folder / ESCALATED_FILE), working_days)
    if all(question.message_id in settled for question in questions):
        return _Wait(questions, settled, set(), set())
    answered = _belonging(folder, REPLIES, questions)
    return _Wait(questions, settled, answered, _belonging(folder, BOUNCES, questions) - answered)


def _settled(questions: list[SentRecord], escalations: list[_Escalation], working_days: int) -> set[str]:
    """The ids of the questions that the escalations cover (_wait)."""
    order = {question.message_id: number for number, question in enumerate(questions)}
    settled: set[str] = set()
    for escalation in escalations:
        if (number := order.get(escalation.message_id)) is None:
            continue
        at = datetime.fromisoformat(escalation.at)
        settled.add(escalation.message_id)
        settled.update(earlier.message_id for earlier in questions[:number]
                       if _past(at, _deadline(datetime.fromisoformat(earlier.sent_at), working_days)))
    return settled


def _belonging(folder: Path, kind: str, questions: list[SentRecord]) -> set[str]:
    """The ids of the questions that the e-mails of the folder `kind` (REPLIES or BOUNCES) of a messages/ folder belong to (_wait)."""
    found: set[str] = set()
    if not (folder / kind).is_dir():
        return found
    known, filed_at = {question.message_id for question in questions}, None
    for path in sorted((folder / kind).iterdir()):
        if not path.is_file():
            continue
        try:
            named = named_ids(path)
        except (ValueError, LookupError):
            continue
        if named:
            question = next((name for name in named if name in known), None)
        else:
            filed_at = _filing_times(folder) if filed_at is None else filed_at
            filed = _filed(path, filed_at.get((kind, path.name)))    # to the second, as a question's own time: only a question sent
            question = next((question.message_id for question in reversed(questions)      # in an earlier second was sent before it
                             if _past(filed, datetime.fromisoformat(question.sent_at))), None)
        if question is not None:
            found.add(question)
    return found


def _filing_times(folder: Path) -> dict[tuple[str, str], str]:
    """When the answers of a case were filed, by the folder each was filed under and its file name: the entries of the list
    "answers" of the case's audit record (file_answer of aid/pipeline.py); none when the record cannot be read."""
    try:
        record = json.loads((folder.parent / AUDIT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    answers = record.get("answers") if isinstance(record, dict) else None
    return {(str(answer.get("filed_under")), str(answer.get("file"))): str(answer.get("at"))
            for answer in (answers if isinstance(answers, list) else []) if isinstance(answer, dict)}


def _filed(path: Path, noted: str | None) -> datetime:
    """When a filed e-mail came into its folder, to the second: the time the audit noted, else the file's own time (when it was
    last changed)."""
    try:
        return datetime.fromisoformat(noted or "").replace(microsecond=0)
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)


def _sent_questions(path: Path) -> list[SentRecord]:
    """The questions of a sent.jsonl in the order sent (a message of an ASK template; a reapproval never counts), read from the end
    of the file. A damaged line on the way to the latest question raises ValueError (a pydantic ValidationError); one before it ends
    the reading there, so that the questions before a damaged line wait no longer."""
    found: list[SentRecord] = []
    for line in reversed(path.read_bytes().split(b"\n")):
        if not line.strip():
            continue
        try:
            record = SentRecord.model_validate_json(line)
        except ValueError:
            if not found:
                raise
            break
        if record.template in QUESTIONS:
            found.append(record)
    return found[::-1]


def pending_asks(cases_root: Path, now: datetime, working_days: int) -> list[str]:
    """The ids of the cases a question of which has its wait ended at now (ended_waits), each once, in the order of their folders; a
    case whose files cannot be read is left out, and ended_waits names it."""
    return list(dict.fromkeys(wait.case_id for wait in ended_waits(cases_root, now, working_days)[0]))


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


def _escalations(path: Path) -> list[_Escalation]:
    """The escalations of a case (escalated.jsonl); none when the file does not exist. A damaged line raises ValueError."""
    if not path.exists():
        return []
    return [_Escalation.model_validate_json(line) for line in path.read_bytes().split(b"\n") if line.strip()]


def _bounce(subject: str, sender: str) -> bool:
    return bool(BOUNCE_SUBJECT.match(subject) or BOUNCE.search(sender))


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
