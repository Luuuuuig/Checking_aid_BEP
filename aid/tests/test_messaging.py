"""Tests of the supplier messages (aid/messaging): the templates, the mailers, the replies and the wait."""
import base64
import json
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import UndefinedError

from aid.case import Case, open_case
from aid.execution.ops import WriteOp
from aid.messaging import (QUESTIONS, REAPPROVAL, EndedWait, FileMailer, MailerError, Message, SentRecord, SmtpMailer, _templates, bounced,
                           ended_waits, file_reply, is_auto_reply, is_bounce, pending_asks, record_escalation, record_sent, render,
                           render_reapproval)
from aid.routing import CaseContext, MessageSpec
from aid.tests.conftest import SETTINGS

SENT = datetime(2026, 11, 20, 16, 0)                 # a Friday


def case_stub(po_number: str) -> Case:
    """A case of the PO as render reads it: its id and its PO number; it has no folder."""
    return Case(case_id=f"{po_number}_20261120T160000", root=Path("unused"), kind="confirmation", po_number=po_number, input_files=[])


def ctx(po_number: str = "250101", supplier: str = "S01") -> CaseContext:
    """The context of a made-up case of the PO and the supplier, as the routing gets it."""
    return CaseContext(supplier=supplier, po_number=po_number, version=1, po_date={}, po_above_authorization=False, supplier_lead_time_days=10,
                       cases_seen_for_supplier=5, document_path="MU01_conf.pdf", kind="confirmation")


def write_eml(path: Path, subject: str, sender: str = "sales@supplier.example.com", auto_submitted: str | None = None) -> Path:
    """A made-up e-mail to the aid's mailbox with this subject, from this sender, with an Auto-Submitted header when one is given,
    saved at path."""
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = sender, "aid@example.com", subject
    if auto_submitted is not None:
        mail["Auto-Submitted"] = auto_submitted
    mail.set_content("A made-up message.")
    path.write_bytes(bytes(mail))
    return path


def test_render_quantity_question(tmp_path):
    msg = render(MessageSpec(template="quantity_unit", items=[{"line_id": "1", "item_code": "KM-1001", "po_value": "3 pc", "doc_value": "5 pc"}]), case_stub("250101"), ctx(), SETTINGS, "sales@supplier.example.com")
    assert msg.subject == "Vraag over orderbevestiging PO 250101" and "KM-1001" in msg.body and msg.cc == [SETTINGS.buyer_email]
    assert render(MessageSpec(template="quantity_unit", items=[]), case_stub("250101"), ctx(), SETTINGS.model_copy(update={"language": "en"}), "supplier@example.com").subject.startswith("Question about order confirmation")

def test_file_mailer_and_pending(tmp_path):
    m = FileMailer(tmp_path, clock=lambda: SENT); rec = m.send(Message(to=["supplier@example.com"], cc=["buyer@example.com"], subject="s", body="b", template="quantity_unit", case_id="250101_20261120T160000"))
    assert (tmp_path / f"{rec.message_id}.eml").exists()
    c = open_case(tmp_path / "cases", "confirmation", [], "250101", datetime(2026, 11, 20, 16, 0)); record_sent(c, rec)
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 15, 0), 3) == [] and pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 17, 0), 3) == [c.case_id]
    file_reply(c, write_eml(tmp_path / "r.eml", subject="Re: s"))
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 26, 9, 0), 3) == []

def test_bounce_is_pending_at_once(tmp_path):
    c = open_case(tmp_path / "cases", "confirmation", [], "250102", datetime(2026, 11, 20, 16, 0)); record_sent(c, SentRecord(message_id="m2", to=["supplier@example.com"], subject="s", sent_at="2026-11-20T16:00:00", template="quantity_unit"))
    b = write_eml(tmp_path / "b.eml", subject="Mail delivery failed: returning message to sender"); assert is_bounce(b)
    file_reply(c, b); assert pending_asks(tmp_path / "cases", datetime(2026, 11, 20, 16, 5), 3) == [c.case_id]


# The tests below cover what the rulings of the task add to the three above.

ITEM = {"po_line_ids": "3", "doc_line_ids": "", "field": "line_presence", "po_value": "KM-1703", "doc_value": "", "line_id": "3",
        "item_code": "KM-1703", "description": "Afdichtingsset pomp"}
CHANGES = [WriteOp(kind="po_line_price", po_number="250108", version=1, line_id="1", old_value="12.50", new_value="12.70"),
           WriteOp(kind="article_price", po_number="250108", version=1, article_code="KM-0801", new_value="12.70")]
ENGLISH = SETTINGS.model_copy(update={"language": "en"})
NAMED = SETTINGS.model_copy(update={"buyer_name": "A. Koper"})          # a made-up name
MESSAGE = Message(to=["sales@supplier.example.com"], cc=["buyer@example.com"], subject="Vraag over orderbevestiging PO 250101",
                  body="Geachte heer, mevrouw,\n\nCoördinatie: één regel.\n", template="quantity_unit", case_id="250101_20261120T160000")


def table(body: str) -> list[list[str]]:
    """The lines of the table of a message, each as its cells: the labels first, then a line per item."""
    return [[cell.strip() for cell in line.split("|")] for line in body.splitlines() if "|" in line]


def read_eml(path: Path) -> EmailMessage:
    return message_from_bytes(path.read_bytes(), policy=policy.default)


def sent_case(tmp_path, po_number: str, *sent: tuple[datetime, str], now: datetime = SENT) -> Case:
    """A case of the PO under tmp_path/cases with a record per message sent: (when, template)."""
    case = open_case(tmp_path / "cases", "confirmation", [], po_number, now)
    for n, (at, template) in enumerate(sent, 1):
        record_sent(case, SentRecord(message_id=f"m{n}", to=["sales@supplier.example.com"], subject="s", sent_at=at.isoformat(), template=template))
    return case


# --- the messages ---

@pytest.mark.parametrize("language", ["nl", "en"])
def test_every_template_renders_with_one_item_and_with_none(language):
    """Each template in each language renders all its variables, with one item (or change) and with none, and signs with the
    buyer's name on the line under the department."""
    settings = NAMED.model_copy(update={"language": language})
    signature = "\n\nInkoop\nA. Koper\n" if language == "nl" else "\n\nPurchasing\nA. Koper\n"
    for template in QUESTIONS:
        for items in ([ITEM], []):
            message = render(MessageSpec(template=template, items=items), case_stub("250117"), ctx("250117", "S17"), settings, "sales@supplier.example.com")
            assert "PO 250117" in message.body and message.body.endswith(signature) and "{" not in message.body, (template, items)
            assert len(table(message.body)) == 1 + len(items)
    for changes in (CHANGES[:1], []):
        message = render_reapproval(changes, ctx("250108", "S08"), settings)
        assert "PO 250108" in message.body and message.body.endswith(signature) and "{" not in message.body
        assert ("12.70" in message.body) == bool(changes)


def test_a_question_in_dutch_and_in_english():
    """A greeting to the supplier, the PO, the table of the items, the question of the template, the wait for a reply and the
    signature; the English message says the same."""
    dutch = render(MessageSpec(template="line_missing_extra", items=[ITEM]), case_stub("250117"), ctx("250117", "S17"), NAMED, "sales@supplier.example.com")
    assert (dutch.to, dutch.cc, dutch.template, dutch.case_id) == (["sales@supplier.example.com"], [SETTINGS.buyer_email], "line_missing_extra",
                                                                    "250117_20261120T160000")
    assert dutch.body.startswith("Geachte heer, mevrouw,\n\nHartelijk dank voor uw orderbevestiging van onze inkooporder PO 250117.")
    assert table(dutch.body) == [["Regel", "Artikelcode", "Omschrijving", "Onze waarde", "Uw waarde"], ["3", "KM-1703", "Afdichtingsset pomp", "KM-1703", "-"]]
    assert "Klopt dit, of is het een vergissing?" in dutch.body
    assert "Wilt u binnen 3 werkdagen antwoorden door op deze e-mail te reageren?" in dutch.body and dutch.body.endswith("Inkoop\nA. Koper\n")
    english = render(MessageSpec(template="line_missing_extra", items=[ITEM]), case_stub("250117"), ctx("250117", "S17"), NAMED.model_copy(update={"language": "en"}),
                     "sales@supplier.example.com")
    assert english.subject == "Question about order confirmation PO 250117" and english.body.startswith("Dear Sir or Madam,\n\nThank you")
    assert table(english.body) == [["Line", "Item code", "Description", "Our value", "Your value"], ["3", "KM-1703", "Afdichtingsset pomp", "KM-1703", "-"]]
    assert "Is this correct, or is it a mistake?" in english.body and "within 3 working days by replying to this e-mail" in english.body
    assert english.body.endswith("Purchasing\nA. Koper\n")


def test_each_question_asks_its_own_question():
    asked = {template: render(MessageSpec(template=template, items=[ITEM]), case_stub("250117"), ctx(), SETTINGS, "s@example.com").body
             for template in QUESTIONS}
    assert "verschil in het aantal of de eenheid" in asked["quantity_unit"] and "Klopt het aantal of de eenheid" in asked["quantity_unit"]
    assert "regels die wij hebben besteld maar die op uw" in asked["line_missing_extra"]
    assert "bijvoorbeeld de Incoterm" in asked["missing_info"] and "Wilt u ons de ontbrekende of de juiste informatie sturen?" in asked["missing_info"]


def test_an_item_shows_its_po_lines_without_a_line_id_and_blanks_without_a_code_or_description():
    """The routing's item, as the pipeline gives it for a line the PO version does not hold (an extra line) or for a question about
    several PO lines: the line is its po_line_ids, and the item code and the description are blank."""
    items = [{"po_line_ids": "1, 2", "doc_line_ids": "1", "field": "quantity", "po_value": "10", "doc_value": "12"},
             {"po_line_ids": "", "doc_line_ids": "3", "field": "line_presence", "po_value": "", "doc_value": "KM-1803"}]
    message = render(MessageSpec(template="quantity_unit", items=items), case_stub("250101"), ctx(), SETTINGS, "s@example.com")
    assert table(message.body)[1:] == [["1, 2", "-", "-", "10", "12"], ["-", "-", "-", "-", "KM-1803"]]


def test_the_wait_and_the_signature_follow_the_settings():
    def body(settings):
        return render(MessageSpec(template="quantity_unit", items=[ITEM]), case_stub("250101"), ctx(), settings, "s@example.com").body

    assert "binnen 1 werkdag antwoorden" in body(SETTINGS.model_copy(update={"ask_wait_working_days": 1}))
    assert "binnen 5 werkdagen antwoorden" in body(SETTINGS.model_copy(update={"ask_wait_working_days": 5}))
    assert "within 1 working day by" in body(ENGLISH.model_copy(update={"ask_wait_working_days": 1}))
    assert body(SETTINGS).endswith("Met vriendelijke groet,\n\nInkoop\n") and body(ENGLISH).endswith("Kind regards,\n\nPurchasing\n")


def test_a_variable_a_template_lacks_fails_the_rendering():
    with pytest.raises(UndefinedError):
        _templates("nl").from_string("PO {{ po_number }}").render(po="250101")
    with pytest.raises(KeyError):
        render(MessageSpec(template="quantity_unit", items=[{"line_id": "1", "po_value": "3"}]), case_stub("250101"), ctx(), SETTINGS, "s@example.com")


def test_the_request_to_the_approver():
    """To the approver, the buyer in copy: each change with its kind in words, its line or article and its old and new value (an
    article's old price, which the routing does not know, is unknown), and why the aid asks. No amount of the settings appears."""
    dutch = render_reapproval(CHANGES, ctx("250108", "S08"), SETTINGS)
    assert (dutch.to, dutch.cc, dutch.subject, dutch.template, dutch.case_id) == (
        [SETTINGS.approver_email], [SETTINGS.buyer_email], "Inkooporder PO 250108 opnieuw ter fiattering", REAPPROVAL, "")
    assert "- Prijs van de orderregel, regel 1: oude waarde 12.50, nieuwe waarde 12.70\n" in dutch.body
    assert "- Artikelprijs, artikel KM-0801: oude waarde onbekend, nieuwe waarde 12.70\n" in dutch.body
    assert "orderbevestiging van leverancier S08 afwijkt van de order" in dutch.body and "boven het bedrag dat de inkoper zelf mag fiatteren" in dutch.body
    english = render_reapproval(CHANGES, ctx("250108", "S08"), ENGLISH)
    assert english.subject == "PO 250108 for approval again" and "- Price of the PO line, line 1: old value 12.50, new value 12.70\n" in english.body
    assert "- Article price, article KM-0801: old value unknown, new value 12.70\n" in english.body
    assert "above the amount that the buyer may approve alone" in english.body
    for amount in (SETTINGS.authorization_limit, SETTINGS.price_abs):
        assert str(amount) not in dutch.body + english.body


# --- the mailers ---

def test_the_file_mailer_writes_a_complete_email(tmp_path):
    """An .eml per message, named by its id: From, To, Cc, Subject, Date, a Message-ID of the id and the sender's domain (aid.invalid
    without a sender), Auto-Submitted, the template and the case in the aid's headers, and the body as plain text in UTF-8. The record
    names the message by the bare id, with the time sent to the second."""
    mailer = FileMailer(tmp_path / "outbox", sender="aid@example.com", clock=lambda: SENT)
    rec = mailer.send(MESSAGE)
    assert uuid.UUID(rec.message_id).hex == rec.message_id and uuid.UUID(rec.message_id).version == 4 and mailer.name == "file"
    assert rec == SentRecord(message_id=rec.message_id, to=MESSAGE.to, subject=MESSAGE.subject, sent_at="2026-11-20T16:00:00", template="quantity_unit")
    mail = read_eml(tmp_path / "outbox" / f"{rec.message_id}.eml")
    assert (mail["From"], mail["To"], mail["Cc"], mail["Subject"]) == ("aid@example.com", "sales@supplier.example.com", "buyer@example.com", MESSAGE.subject)
    assert (mail["X-Aid-Template"], mail["X-Aid-Case"]) == ("quantity_unit", "250101_20261120T160000")
    assert (mail["Message-ID"], mail["Auto-Submitted"]) == (f"<{rec.message_id}@example.com>", "auto-generated")
    assert parsedate_to_datetime(mail["Date"]) == SENT.astimezone()            # a time without a zone is local time
    assert mail.get_content_type() == "text/plain" and mail.get_content_charset() == "utf-8" and mail.get_content() == MESSAGE.body
    second = FileMailer(tmp_path / "outbox").send(MESSAGE)
    unsigned = read_eml(tmp_path / "outbox" / f"{second.message_id}.eml")
    assert second.message_id != rec.message_id and unsigned["From"] is None and unsigned["Message-ID"] == f"<{second.message_id}@aid.invalid>"


@pytest.fixture
def smtp(monkeypatch):
    """smtplib.SMTP replaced by a fake that records its calls and connects to nothing. fail maps a step (connect, starttls, login,
    send_message, quit) to the error the fake raises there; context is the TLS context starttls was given; refused is what
    send_message returns, the recipients the server refused while it accepted others."""
    fake = SimpleNamespace(calls=[], fail={}, context=None, refused={})

    class FakeSmtp:
        def __init__(self, host, port, timeout=None):
            self.step("connect", host, port, timeout)

        def step(self, name, *args):
            fake.calls.append((name, *args))
            if name in fake.fail:
                raise fake.fail[name]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.step("quit")

        def starttls(self, context=None):
            fake.context = context
            self.step("starttls")

        def login(self, user, password):
            self.step("login", user, password)

        def send_message(self, mail):
            self.step("send_message", mail)
            return fake.refused

    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return fake


SECRET = uuid.uuid4().hex                            # made up for each test session: no password is written down


def test_the_smtp_mailer_sends_with_starttls_and_the_password_of_the_environment(smtp, monkeypatch):
    """The password is read from the environment when the message is sent, not when the mailer is made, and the mailer keeps none.
    STARTTLS verifies the server's certificate and name. The sender is the login unless one is given, and the Message-ID takes the
    sender's domain, aid.invalid for a login that is no address."""
    mailer = SmtpMailer("smtp.example.com", 587, "aid@example.com", clock=lambda: SENT)
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    rec = mailer.send(MESSAGE)
    mail = smtp.calls[3][1]
    assert smtp.calls == [("connect", "smtp.example.com", 587, 30), ("starttls",), ("login", "aid@example.com", SECRET), ("send_message", mail),
                          ("quit",)]
    assert mailer.name == "smtp" and SECRET not in repr(vars(mailer))
    assert isinstance(smtp.context, ssl.SSLContext) and smtp.context.verify_mode == ssl.CERT_REQUIRED and smtp.context.check_hostname
    assert (mail["From"], mail["To"], mail["Cc"], mail["Subject"], mail["X-Aid-Case"]) == (
        "aid@example.com", "sales@supplier.example.com", "buyer@example.com", MESSAGE.subject, MESSAGE.case_id)
    assert (mail["Message-ID"], mail["Auto-Submitted"]) == (f"<{rec.message_id}@example.com>", "auto-generated")
    assert (rec.to, rec.subject, rec.sent_at, rec.template, len(rec.message_id), rec.refused_cc) == (
        MESSAGE.to, MESSAGE.subject, "2026-11-20T16:00:00", "quantity_unit", 32, 0)
    monkeypatch.setenv("OTHER_PASSWORD", SECRET)
    SmtpMailer("smtp.example.com", 587, "aid@example.com", password_env="OTHER_PASSWORD", sender="Inkoop <inkoop@purchasing.example.org>").send(MESSAGE)
    assert smtp.calls[-3] == ("login", "aid@example.com", SECRET) and smtp.calls[-2][1]["From"] == "Inkoop <inkoop@purchasing.example.org>"
    assert smtp.calls[-2][1]["Message-ID"].endswith("@purchasing.example.org>")
    rec = SmtpMailer("smtp.example.com", 587, "aidlogin").send(MESSAGE)
    assert smtp.calls[-3] == ("login", "aidlogin", SECRET) and smtp.calls[-2][1]["Message-ID"] == f"<{rec.message_id}@aid.invalid>"


def test_a_refusal_during_rcpt_before_every_recipient_is_no_send(smtp, monkeypatch):
    """smtplib raises SMTPRecipientsRefused also when the server answers 421 during RCPT, before DATA, with only the recipients seen
    so far: the To accepted and the Cc answered 421 is no send but a MailerError of the class and the step, with no address."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.fail["send_message"] = smtplib.SMTPRecipientsRefused({"buyer@example.com": (421, b"4.3.2 buyer@example.com: closing")})
    with pytest.raises(MailerError) as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert str(raised.value) == "SMTPRecipientsRefused: could not send the message through smtp.example.com:587"
    assert raised.value.__suppress_context__ and "buyer" not in str(raised.value)


def test_a_refused_to_recipient_fails_the_send_and_a_refused_copy_is_counted(smtp, monkeypatch):
    """The server accepts the message for some recipients and refuses others. A refused To recipient (the supplier) fails the send,
    with the counts and no address; a refused Cc (the buyer's copy) alone does not, and the record counts it. A server that refuses
    every recipient refuses the To recipients too."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    mailer = SmtpMailer("smtp.example.com", 587, "aid@example.com")
    smtp.refused = {"sales@supplier.example.com": (550, b"5.1.1 <sales@supplier.example.com>: no such user")}
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 1 To recipients refused$"):
        mailer.send(MESSAGE)
    two = MESSAGE.model_copy(update={"to": ["sales@supplier.example.com", "orders@supplier.example.com"]})
    smtp.refused = {"orders@supplier.example.com": (550, b"5.1.1 no such user")}
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 2 To recipients refused$"):
        mailer.send(two)
    smtp.refused = {"Buyer@Example.com": (452, b"4.2.2 mailbox full")}                   # addresses compared in lower case
    assert mailer.send(MESSAGE).refused_cc == 1
    smtp.refused, smtp.fail["send_message"] = {}, smtplib.SMTPRecipientsRefused(
        {address: (550, b"5.7.1 relaying denied") for address in ("sales@supplier.example.com", "buyer@example.com")})
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 1 To recipients refused$") as raised:
        mailer.send(MESSAGE)
    assert "example.com" not in str(raised.value) and "relaying" not in str(raised.value)


@pytest.mark.parametrize("step, error, text", [
    ("connect", ConnectionRefusedError(111, "Connection refused"), "ConnectionRefusedError: could not connect to smtp.example.com:587"),
    ("starttls", smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."),
     "SMTPNotSupportedError: could not start TLS with smtp.example.com:587"),
    ("login", smtplib.SMTPAuthenticationError(535, f"5.7.8 aid@example.com {SECRET} not accepted".encode()),
     "SMTPAuthenticationError: could not log in to smtp.example.com:587 (SMTP code 535)"),
    ("send_message", smtplib.SMTPSenderRefused(553, b"aid@example.com refused", "aid@example.com"),
     "SMTPSenderRefused: could not send the message through smtp.example.com:587 (SMTP code 553)"),
])
def test_what_the_smtp_mailer_cannot_send_is_a_mailer_error_without_the_login_or_the_password(smtp, monkeypatch, step, error, text):
    """The error names its class, the step and the server: not its own text, which may quote the login or the password, nor the
    error itself as a cause."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.fail[step] = error
    with pytest.raises(MailerError) as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert str(raised.value) == text and raised.value.__cause__ is None and raised.value.__suppress_context__
    assert "aid@example.com" not in str(raised.value) and SECRET not in str(raised.value)


def test_the_smtp_mailer_needs_its_password_and_a_closing_error_does_not_unsend(smtp, monkeypatch):
    monkeypatch.delenv("AID_MAIL_PASSWORD", raising=False)
    with pytest.raises(MailerError, match="^KeyError: the environment variable AID_MAIL_PASSWORD is not set$"):
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert smtp.calls == []                                                    # no connection without the password
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.fail["quit"] = smtplib.SMTPResponseException(421, b"closing")
    assert SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE).to == MESSAGE.to   # the message went before the error


# --- the case folder ---

def test_record_sent_appends_a_line_per_message(tmp_path):
    """The folder is made when it is missing, and a line cut off by a crash leaves the next record on a line of its own. The wait reads
    sent.jsonl from its end back to the latest question, so a damaged line before it is never read; a damaged last line makes the
    case unreadable, without an error."""
    case = Case(case_id="250101_20261120T160000", root=tmp_path / "cases" / "250101_20261120T160000", kind="confirmation", po_number="250101", input_files=[])
    first = SentRecord(message_id="m1", to=["s@example.com"], subject="s", sent_at="2026-11-20T16:00:00", template="quantity_unit")
    record_sent(case, first)
    record_sent(case, first.model_copy(update={"message_id": "m2"}))
    path = case.root / "messages" / "sent.jsonl"
    assert [json.loads(line)["message_id"] for line in path.read_text(encoding="utf-8").splitlines()] == ["m1", "m2"]
    path.write_bytes(path.read_bytes() + b'{"message_id": "m3", "to"')        # cut off
    record_sent(case, first.model_copy(update={"message_id": "m4"}))
    assert path.read_text(encoding="utf-8").splitlines()[-1] == first.model_copy(update={"message_id": "m4"}).model_dump_json()
    assert ended_waits(tmp_path / "cases", SENT + timedelta(days=30), 3) == ([EndedWait(case.case_id, "m4", False)], [])
    path.write_bytes(path.read_bytes() + b'{"message_id": "m5", "to"')        # cut off at the end
    assert ended_waits(tmp_path / "cases", SENT + timedelta(days=30), 3) == ([], [(case.case_id, "ValidationError")])
    assert pending_asks(tmp_path / "cases", SENT + timedelta(days=30), 3) == []


def test_file_reply_copies_a_reply_or_a_bounce_under_a_free_name(tmp_path):
    case = open_case(tmp_path / "cases", "confirmation", [], "250101", SENT)
    reply = write_eml(tmp_path / "r.eml", subject="Re: Vraag over orderbevestiging PO 250101")
    paths = [file_reply(case, reply) for _ in range(3)]
    assert paths == [case.root / "messages" / "replies" / name for name in ("r.eml", "r_2.eml", "r_3.eml")]
    assert reply.exists() and all(path.read_bytes() == reply.read_bytes() for path in paths) and not bounced(case)
    bounce = write_eml(tmp_path / "r.eml", subject="Undeliverable: Vraag over orderbevestiging PO 250101")
    assert file_reply(case, bounce) == case.root / "messages" / "bounces" / "r.eml" and bounced(case)


@pytest.mark.parametrize("subject, sender, expected", [
    ("Mail delivery failed: returning message to sender", "sales@supplier.example.com", True),
    ("Undeliverable: Vraag over orderbevestiging PO 250101", "postmaster@example.com", True),
    ("DELIVERY STATUS NOTIFICATION (Failure)", "postmaster@example.com", True),
    ("Re: Vraag over orderbevestiging PO 250101", "Mail Delivery System <Mailer-Daemon@mail.example.com>", True),
    ("Onbestelbaar: Vraag over orderbevestiging PO 250101", "postmaster@example.com", True),
    ("Niet bezorgd: Vraag over orderbevestiging PO 250101", "postmaster@example.com", True),
    ("Uw bericht kon niet worden bezorgd", "postmaster@example.com", True),
    ("Unzustellbar: Vraag over orderbevestiging PO 250101", "postmaster@example.com", True),
    ("Nachricht wurde nicht zugestellt", "postmaster@example.com", True),
    ("Zustellung fehlgeschlagen", "postmaster@example.com", True),
    ("Re: Vraag over orderbevestiging PO 250101", "sales@supplier.example.com", False),
    ("Delivery of your order", "sales@supplier.example.com", False),
])
def test_is_bounce(tmp_path, subject, sender, expected):
    assert is_bounce(write_eml(tmp_path / "m.eml", subject=subject, sender=sender)) is expected


def test_an_email_without_a_subject(tmp_path):
    path = tmp_path / "m.eml"
    path.write_bytes(b"From: sales@supplier.example.com\nTo: aid@example.com\n\nA made-up message.\n")
    assert not is_bounce(path) and not is_auto_reply(path)
    path.write_bytes(b"From: MAILER-DAEMON@mail.example.com\nTo: aid@example.com\n\nA made-up bounce.\n")
    assert is_bounce(path) and not is_auto_reply(path)


@pytest.mark.parametrize("subject, auto_submitted, expected", [
    ("Automatic reply: Vraag over orderbevestiging PO 250101", None, True),
    ("Automatisch antwoord: Vraag over orderbevestiging PO 250101", None, True),
    ("Automatische Antwort: Ihre Anfrage", None, True),
    ("Out of Office: Vraag over orderbevestiging PO 250101", None, True),
    ("Afwezig tot maandag 30 november", None, True),
    ("Abwesenheitsnotiz: Vraag over orderbevestiging PO 250101", None, True),
    ("Re: Vraag over orderbevestiging PO 250101", "auto-replied", True),
    ("Re: Vraag over orderbevestiging PO 250101", "Auto-Generated; owner-email=info@supplier.example.com", True),
    ("Re: Vraag over orderbevestiging PO 250101", "no", False),
    ("Re: Vraag over orderbevestiging PO 250101", "No", False),
    ("Re: Vraag over orderbevestiging PO 250101", None, False),
    ("Undeliverable: Vraag over orderbevestiging PO 250101", "auto-replied", False),         # a bounce stays a bounce
])
def test_is_auto_reply(tmp_path, subject, auto_submitted, expected):
    assert is_auto_reply(write_eml(tmp_path / "m.eml", subject=subject, auto_submitted=auto_submitted)) is expected


@pytest.mark.parametrize("subject, expected", [
    ("Re: Vraag over orderbevestiging PO 250101 - afwezigheid van artikel KM-1001", False),
    ("Re: PO 250101: Out of office furniture line 2", False),
    ("Automatisch antwoord: Vraag over orderbevestiging PO 250101", True),
    ("RE: Fwd:Automatic reply: Vraag over orderbevestiging PO 250101", True),
    ("AW: Antw: FW: Abwesenheitsnotiz", True),
])
def test_the_subject_of_an_automatic_reply_begins_with_its_words(tmp_path, subject, expected):
    """The words of an automatic reply count at the start of the subject, after any reply or forward prefixes; a real reply that
    mentions them further on is a reply."""
    assert is_auto_reply(write_eml(tmp_path / "m.eml", subject=subject)) is expected


def test_an_automatic_reply_is_filed_apart_and_ends_no_wait(tmp_path):
    """An out-of-office notice is neither a reply nor a bounce: file_reply puts it in messages/auto/, and the case waits until its
    deadline as if nothing had come."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    notice = write_eml(tmp_path / "ooo.eml", subject="Afwezig: Vraag over orderbevestiging PO 250101", sender="j.jansen@supplier.example.com")
    assert file_reply(case, notice) == case.root / "messages" / "auto" / "ooo.eml" and not bounced(case)
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 20, 16, 5), 3) == []
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 25, 16, 1), 3) == ([EndedWait(case.case_id, "m1", False)], [])


def test_is_bounce_reads_an_encoded_subject(tmp_path):
    """A subject in an encoded word, which only the e-mail package's decoding shows as text."""
    encoded = base64.b64encode("Undeliverable: Vraag over orderbevestiging PO 250101".encode()).decode()
    path = tmp_path / "m.eml"
    path.write_bytes(f"From: postmaster@example.com\nTo: aid@example.com\nSubject: =?utf-8?b?{encoded}?=\n\nA made-up bounce.\n".encode())
    assert "Undeliverable" not in path.read_text(encoding="ascii") and is_bounce(path)


def test_the_wait_skips_the_weekend_and_keeps_the_time_of_day(tmp_path):
    """Sent on Friday at 16:00 with three working days, the deadline is Wednesday at 16:00: at that moment the case still waits."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 16, 0), 3) == []
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 16, 0, 1), 3) == [case.case_id]
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 23, 16, 0, 1), 1) == [case.case_id]      # one working day: Monday
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 20, 16, 0, 1), 0) == [case.case_id]


def test_the_latest_question_counts_and_a_reapproval_never(tmp_path):
    """The wait runs from the latest question; a request to the approver is no question, before it or after it."""
    asked_twice = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (datetime(2026, 11, 23, 16, 0), "missing_info"),
                            (datetime(2026, 11, 24, 9, 0), REAPPROVAL))
    sent_case(tmp_path, "250108", (SENT - timedelta(days=30), REAPPROVAL))
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 26, 16, 0), 3) == []                     # Monday plus three: Thursday
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 26, 16, 1), 3) == [asked_twice.case_id]


def test_pending_cases_are_in_folder_order_and_an_escalated_case_is_not_pending_again(tmp_path):
    later = sent_case(tmp_path, "250102", (SENT, "line_missing_extra"))
    earlier = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    (tmp_path / "cases" / "not_a_case").mkdir()
    now = datetime(2026, 12, 1, 9, 0)
    assert pending_asks(tmp_path / "cases", now, 3) == [earlier.case_id, later.case_id]
    record_escalation(earlier, "no answer in 3 working days", now)                           # the latest question: m1
    assert pending_asks(tmp_path / "cases", now, 3) == [later.case_id]
    assert [json.loads(line) for line in (earlier.root / "messages" / "escalated.jsonl").read_text(encoding="utf-8").splitlines()] == [
        {"case_id": earlier.case_id, "message_id": "m1", "reason": "no answer in 3 working days", "at": now.isoformat()}]
    assert pending_asks(tmp_path / "nothing", now, 3) == []
    record_sent(earlier, SentRecord(message_id="m2", to=["s@example.com"], subject="s", sent_at=now.isoformat(), template="quantity_unit"))
    assert pending_asks(tmp_path / "cases", datetime(2026, 12, 4, 9, 0), 3) == [later.case_id]       # Tuesday 1 December plus three
    assert pending_asks(tmp_path / "cases", datetime(2026, 12, 4, 9, 1), 3) == [earlier.case_id, later.case_id]
    record_escalation(earlier, "no answer in 3 working days", datetime(2026, 12, 4, 9, 1), "m2")
    assert pending_asks(tmp_path / "cases", datetime(2026, 12, 4, 9, 1), 3) == [later.case_id]


def test_a_damaged_file_names_its_case_and_the_sweep_goes_on(tmp_path):
    """A torn last line in one case's sent.jsonl, a damaged escalated.jsonl in another and a sent.jsonl that is a folder in a third:
    each is named with the class of its error, and the case beside them is pending all the same."""
    torn = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    sent = torn.root / "messages" / "sent.jsonl"
    sent.write_bytes(sent.read_bytes() + b'{"message_id": "m2", "to"')
    damaged = sent_case(tmp_path, "250102", (SENT, "quantity_unit"))
    (damaged.root / "messages" / "escalated.jsonl").write_text("not json\n", encoding="utf-8")
    folder = open_case(tmp_path / "cases", "confirmation", [], "250103", SENT)
    (folder.root / "messages" / "sent.jsonl").mkdir()
    valid = sent_case(tmp_path, "250104", (SENT, "missing_info"))
    now = datetime(2026, 12, 1, 9, 0)
    assert ended_waits(tmp_path / "cases", now, 3) == ([EndedWait(valid.case_id, "m1", False)], [
        (torn.case_id, "ValidationError"), (damaged.case_id, "ValidationError"), (folder.case_id, "IsADirectoryError")])
    assert pending_asks(tmp_path / "cases", now, 3) == [valid.case_id]


def test_a_time_with_a_zone_and_one_without_compare(tmp_path):
    """A clock with a zone (the batch's) and a record without one (a file mailer's default clock): the time without a zone is local."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 15, 59).astimezone(), 3) == []
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 16, 1).astimezone(), 3) == [case.case_id]
    aware = sent_case(tmp_path, "250102", (SENT.astimezone(), "quantity_unit"))
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 25, 16, 1), 3) == [case.case_id, aware.case_id]
