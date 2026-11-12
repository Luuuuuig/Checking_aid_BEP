"""Tests of the supplier messages (aid/messaging): the templates, the mailers, the replies and the wait."""
import base64
import json
import os
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
from aid.messaging import (QUESTIONS, REAPPROVAL, EndedWait, FileMailer, HandOverUnconfirmed, MailerError, Message, SentRecord, SmtpMailer,
                           _templates, bounced, bounced_questions, ended_waits, file_reply, is_auto_reply, is_bounce, kind_of, named_ids,
                           open_questions, pending_asks, record_escalation, record_sent, render, render_reapproval)
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


def at_time(path: Path, when: datetime) -> Path:
    """The file with `when` as its own time, which file_reply's copy keeps: an e-mail that names no message belongs to the latest
    question sent before it was filed, and the made-up questions of these tests are sent at fixed times."""
    os.utime(path, (when.timestamp(), when.timestamp()))
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
    file_reply(c, at_time(write_eml(tmp_path / "r.eml", subject="Re: s"), datetime(2026, 11, 25, 18, 0)))
    assert pending_asks(tmp_path / "cases", datetime(2026, 11, 26, 9, 0), 3) == []

def test_bounce_is_pending_at_once(tmp_path):
    c = open_case(tmp_path / "cases", "confirmation", [], "250102", datetime(2026, 11, 20, 16, 0)); record_sent(c, SentRecord(message_id="m2", to=["supplier@example.com"], subject="s", sent_at="2026-11-20T16:00:00", template="quantity_unit"))
    b = write_eml(tmp_path / "b.eml", subject="Mail delivery failed: returning message to sender"); assert is_bounce(b)
    file_reply(c, at_time(b, datetime(2026, 11, 20, 16, 2))); assert pending_asks(tmp_path / "cases", datetime(2026, 11, 20, 16, 5), 3) == [c.case_id]


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


@pytest.mark.parametrize("language, of_a_cart, of_a_confirmation", [
    ("nl", "Inkooporder PO 250108 wordt gewijzigd, omdat de winkelwagen bij leverancier S08 afwijkt van de order:",
     "Inkooporder PO 250108 wordt gewijzigd, omdat de orderbevestiging van leverancier S08 afwijkt van de order:"),
    ("en", "Purchase order PO 250108 is being changed, because the cart at supplier S08 differs from the order:",
     "Purchase order PO 250108 is being changed, because the order confirmation of supplier S08 differs from the order:")])
def test_the_request_to_the_approver_of_a_carts_case_names_the_cart(language, of_a_cart, of_a_confirmation):
    """Task 41, ruling Q19: for a case of the kind cart no order confirmation exists yet, so the request's reason names the cart at
    the supplier. Nothing else of the request differs, and the request of a confirmation's case is what it was, byte for byte."""
    settings = NAMED.model_copy(update={"language": language})
    confirmation = render_reapproval(CHANGES, ctx("250108", "S08"), settings)
    cart = render_reapproval(CHANGES, ctx("250108", "S08").model_copy(update={"kind": "cart"}), settings)
    assert of_a_cart in " ".join(cart.body.split()) and of_a_confirmation not in " ".join(cart.body.split())
    assert of_a_confirmation in " ".join(confirmation.body.split()) and of_a_cart not in " ".join(confirmation.body.split())
    theirs, ours = confirmation.body.split("\n\n"), cart.body.split("\n\n")
    assert len(theirs) == len(ours) and [n for n, (a, b) in enumerate(zip(theirs, ours)) if a != b] == [1]      # the one sentence
    assert max(len(line) for line in cart.body.splitlines() if not line.startswith("- ")) <= 78
    assert (cart.subject, cart.to, cart.cc, cart.template) == (confirmation.subject, confirmation.to, confirmation.cc, confirmation.template)
    old = {"nl": "Beste collega,\n\nInkooporder PO 250108 wordt gewijzigd, omdat de\norderbevestiging van leverancier S08 afwijkt van de order:\n\n",
           "en": "Dear colleague,\n\nPurchase order PO 250108 is being changed, because the order\nconfirmation of supplier S08 differs from the order:\n\n"}
    assert confirmation.body.startswith(old[language])


@pytest.mark.parametrize("language, changed, asked", [
    ("nl", "Inkooporder PO 250108 wordt gewijzigd, omdat de orderbevestiging van leverancier S08 afwijkt van de order:",
     "De order ligt boven het bedrag dat de inkoper zelf mag fiatteren. Wilt u de order opnieuw fiatteren zodra de wijziging in Exact staat?"),
    ("en", "Purchase order PO 250108 is being changed, because the order confirmation of supplier S08 differs from the order:",
     "The order is above the amount that the buyer may approve alone. Could you approve the order again once the change is in Exact?")])
def test_the_request_to_the_approver_is_true_for_a_prepared_change(language, changed, asked):
    """Every change is prepared and the buyer enters it in Exact: the request says that the order is being changed and asks to approve
    it again once the change is in Exact, and no longer that the aid changed it (Task 35, rule 11). The rest of the request, its
    greeting, its changes and its signature, is what it was, and its lines keep the width of the other templates."""
    settings = NAMED.model_copy(update={"language": language})
    message = render_reapproval(CHANGES, ctx("250108", "S08"), settings)
    flat = " ".join(message.body.split())
    assert changed in flat and asked in flat
    for said in ("heeft inkooporder", "gewijzigde order", "has changed", "the changed order", "inkoopassistent heeft", "assistant has"):
        assert said not in flat, said
    greeting, change, signature = (("Beste collega,\n\n", "- Prijs van de orderregel, regel 1: oude waarde 12.50, nieuwe waarde 12.70\n",
                                    "\n\nMet vriendelijke groet,\n\nInkoop\nA. Koper\n") if language == "nl" else
                                   ("Dear colleague,\n\n", "- Price of the PO line, line 1: old value 12.50, new value 12.70\n",
                                    "\n\nKind regards,\n\nPurchasing\nA. Koper\n"))
    assert message.body.startswith(greeting) and change in message.body and message.body.endswith(signature)
    assert max(len(line) for line in message.body.splitlines() if not line.startswith("- ")) <= 76     # as wide as the other templates


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
    """smtplib.SMTP replaced by a fake that records its calls and connects to nothing. The mailer gives the message by hand (MAIL,
    RCPT for each recipient, DATA, the message, the server's answer), so that it can stop before the message is given. fail maps a
    step (connect, starttls, login, mail, rcpt, data, body, reply, quit) to the error the fake raises there; context is the TLS
    context starttls was given; refused maps a recipient, in lower case, to the answer of the server that refuses it at RCPT; answers
    gives the server's answer to "mail", to "data" (354 unless given) and to the message given ("reply")."""
    fake = SimpleNamespace(calls=[], fail={}, context=None, refused={}, answers={})

    class FakeSmtp:
        does_esmtp = True

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

        def ehlo_or_helo_if_needed(self):
            pass

        def has_extn(self, name):
            return False

        def mail(self, sender, options=()):
            self.step("mail", sender)
            return fake.answers.get("mail", (250, b"2.1.0 ok"))

        def rcpt(self, recipient, options=()):
            self.step("rcpt", recipient)
            return {address.lower(): answer for address, answer in fake.refused.items()}.get(recipient.lower(), (250, b"2.1.5 ok"))

        def docmd(self, command, args=""):
            self.step(command)
            return fake.answers.get(command, (354, b"go ahead") if command == "data" else (250, b"2.0.0 ok"))

        def send(self, data):
            self.step("body", data)

        def getreply(self):
            self.step("reply")
            return fake.answers.get("reply", (250, b"2.0.0 queued"))

        def close(self):
            self.step("close")

    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return fake


SECRET = uuid.uuid4().hex                            # made up for each test session: no password is written down
GIVEN = [("mail", "aid@example.com"), ("rcpt", "sales@supplier.example.com"), ("rcpt", "buyer@example.com"), ("data",)]


def given(smtp, number: int = -1):
    """The e-mail of the fake's call that gave a message to the server (the last one unless a number is given), parsed."""
    bodies = [call[1] for call in smtp.calls if call[0] == "body"]
    assert bodies[number].endswith(b"\r\n.\r\n")
    return message_from_bytes(bodies[number][:-3], policy=policy.default)


def test_the_smtp_mailer_sends_with_starttls_and_the_password_of_the_environment(smtp, monkeypatch):
    """The password is read from the environment when the message is sent, not when the mailer is made, and the mailer keeps none.
    STARTTLS verifies the server's certificate and name. The sender is the login unless one is given, and the Message-ID takes the
    sender's domain, aid.invalid for a login that is no address."""
    mailer = SmtpMailer("smtp.example.com", 587, "aid@example.com", clock=lambda: SENT)
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    rec = mailer.send(MESSAGE)
    assert smtp.calls == [("connect", "smtp.example.com", 587, 30), ("starttls",), ("login", "aid@example.com", SECRET), *GIVEN,
                          ("body", smtp.calls[7][1]), ("reply",), ("quit",)]
    mail = given(smtp)
    assert mailer.name == "smtp" and SECRET not in repr(vars(mailer))
    assert isinstance(smtp.context, ssl.SSLContext) and smtp.context.verify_mode == ssl.CERT_REQUIRED and smtp.context.check_hostname
    assert (mail["From"], mail["To"], mail["Cc"], mail["Subject"], mail["X-Aid-Case"]) == (
        "aid@example.com", "sales@supplier.example.com", "buyer@example.com", MESSAGE.subject, MESSAGE.case_id)
    assert (mail["Message-ID"], mail["Auto-Submitted"]) == (f"<{rec.message_id}@example.com>", "auto-generated")
    assert (rec.to, rec.subject, rec.sent_at, rec.template, len(rec.message_id), rec.refused_cc) == (
        MESSAGE.to, MESSAGE.subject, "2026-11-20T16:00:00", "quantity_unit", 32, 0)
    assert "unconfirmed" not in rec.model_dump_json() and b"\n" not in smtp.calls[7][1].replace(b"\r\n", b"")     # CRLF line ends only
    monkeypatch.setenv("OTHER_PASSWORD", SECRET)
    SmtpMailer("smtp.example.com", 587, "aid@example.com", password_env="OTHER_PASSWORD", sender="Inkoop <inkoop@purchasing.example.org>").send(MESSAGE)
    assert ("login", "aid@example.com", SECRET) in smtp.calls[10:] and ("mail", "inkoop@purchasing.example.org") in smtp.calls[10:]
    assert given(smtp)["From"] == "Inkoop <inkoop@purchasing.example.org>" and given(smtp)["Message-ID"].endswith("@purchasing.example.org>")
    rec = SmtpMailer("smtp.example.com", 587, "aidlogin").send(MESSAGE)
    assert smtp.calls[-8] == ("login", "aidlogin", SECRET) and given(smtp)["Message-ID"] == f"<{rec.message_id}@aid.invalid>"


def test_a_line_that_begins_with_a_period_is_given_as_smtp_asks(smtp, monkeypatch):
    """The message is given as SMTP asks: a line of the body that begins with a period gets a second one, which the server takes
    off again, and the message ends with a line that holds one period."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE.model_copy(update={"body": "Regel een\n.verborgen regel\nRegel drie\n"}))
    body = smtp.calls[7][1]
    assert b"\r\n..verborgen regel\r\n" in body and body.endswith(b"Regel drie\r\n.\r\n")


def test_a_refusal_during_rcpt_before_every_recipient_is_no_send(smtp, monkeypatch):
    """The server answers 421 to a recipient and closes: no send, a MailerError of the class and the step, with no address, of the
    kind that another try may get past. The message is not given."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.refused = {"buyer@example.com": (421, b"4.3.2 buyer@example.com: closing")}
    with pytest.raises(MailerError) as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert str(raised.value) == "SMTPRecipientsRefused: could not send the message through smtp.example.com:587"
    assert raised.value.__suppress_context__ and "buyer" not in str(raised.value) and raised.value.kind == "unreachable"
    assert ("data",) not in smtp.calls and ("close",) in smtp.calls


def test_a_refused_to_recipient_fails_the_send_and_a_refused_copy_is_counted(smtp, monkeypatch):
    """A To recipient the server refuses (the supplier) fails the send, with the counts and no address, before the message is given:
    it goes to nobody, the buyer's copy included (ruling S10 of the review round of Task 44: the buyer held a copy of a question the
    supplier never got, and a second one after the retry). A refused Cc (the buyer's copy) alone does not fail it: the supplier has
    the question, and the record counts the refusal. A server that refuses every recipient refuses the To recipients too."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    mailer = SmtpMailer("smtp.example.com", 587, "aid@example.com")
    smtp.refused = {"sales@supplier.example.com": (550, b"5.1.1 <sales@supplier.example.com>: no such user")}
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 1 To recipients refused$") as raised:
        mailer.send(MESSAGE)
    assert raised.value.kind == "refused" and smtp.calls[3:] == [*GIVEN[:3], ("rset",), ("quit",)]       # every recipient asked, nothing given
    two = MESSAGE.model_copy(update={"to": ["sales@supplier.example.com", "orders@supplier.example.com"]})
    smtp.refused = {"orders@supplier.example.com": (550, b"5.1.1 no such user")}
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 2 To recipients refused$"):
        mailer.send(two)
    assert [call[0] for call in smtp.calls].count("body") == 0
    smtp.refused = {"Buyer@Example.com": (452, b"4.2.2 mailbox full")}                   # addresses compared in lower case
    assert mailer.send(MESSAGE).refused_cc == 1 and given(smtp)["To"] == "sales@supplier.example.com"
    smtp.refused = {address: (550, b"5.7.1 relaying denied") for address in ("sales@supplier.example.com", "buyer@example.com")}
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 1 To recipients refused$") as raised:
        mailer.send(MESSAGE)
    assert "example.com" not in str(raised.value) and "relaying" not in str(raised.value)
    smtp.refused = {"sales@supplier.example.com": (450, b"4.2.1 mailbox busy")}             # a refusal for now: another try may pass
    with pytest.raises(MailerError, match=r"^SMTPRecipientsRefused: 1 of 1 To recipients refused$") as raised:
        mailer.send(MESSAGE)
    assert raised.value.kind == "unreachable" and [call[0] for call in smtp.calls].count("body") == 1


@pytest.mark.parametrize("step, error, text, kind", [
    ("connect", ConnectionRefusedError(111, "Connection refused"), "ConnectionRefusedError: could not connect to smtp.example.com:587", "unreachable"),
    ("connect", TimeoutError("timed out"), "TimeoutError: could not connect to smtp.example.com:587", "unreachable"),
    ("starttls", smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."),
     "SMTPNotSupportedError: could not start TLS with smtp.example.com:587", "refused"),
    ("starttls", ssl.SSLCertVerificationError(1, "certificate verify failed"),
     "SSLCertVerificationError: could not start TLS with smtp.example.com:587", "refused"),
    ("login", smtplib.SMTPAuthenticationError(535, f"5.7.8 aid@example.com {SECRET} not accepted".encode()),
     "SMTPAuthenticationError: could not log in to smtp.example.com:587 (SMTP code 535)", "refused"),
    ("login", smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
     "SMTPServerDisconnected: could not log in to smtp.example.com:587", "unreachable"),
    ("data", smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
     "SMTPServerDisconnected: could not send the message through smtp.example.com:587", "unreachable"),
])
def test_what_the_smtp_mailer_cannot_send_is_a_mailer_error_without_the_login_or_the_password(smtp, monkeypatch, step, error, text, kind):
    """The error names its class, the step and the server: not its own text, which may quote the login or the password, nor the
    error itself as a cause. Its kind says whether another try may get past it ("unreachable": no connection, a time-out) or not
    ("refused"). A failure before the message is given is a message not sent, also one at the DATA command itself."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.fail[step] = error
    with pytest.raises(MailerError) as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert str(raised.value) == text and raised.value.__cause__ is None and raised.value.__suppress_context__
    assert "aid@example.com" not in str(raised.value) and SECRET not in str(raised.value)
    assert raised.value.kind == kind and not isinstance(raised.value, HandOverUnconfirmed) and "body" not in [call[0] for call in smtp.calls]


@pytest.mark.parametrize("answers, text, kind", [
    ({"mail": (553, b"aid@example.com refused")}, "SMTPSenderRefused: could not send the message through smtp.example.com:587 (SMTP code 553)", "refused"),
    ({"mail": (451, b"try again later")}, "SMTPSenderRefused: could not send the message through smtp.example.com:587 (SMTP code 451)", "unreachable"),
    ({"data": (554, b"no")}, "SMTPDataError: could not send the message through smtp.example.com:587 (SMTP code 554)", "refused"),
    ({"reply": (554, b"5.7.1 message refused")}, "SMTPDataError: could not send the message through smtp.example.com:587 (SMTP code 554)", "refused"),
    ({"reply": (451, b"4.3.0 try again later")}, "SMTPDataError: could not send the message through smtp.example.com:587 (SMTP code 451)", "unreachable"),
])
def test_an_answer_of_the_server_that_refuses_the_message_is_a_message_not_sent(smtp, monkeypatch, answers, text, kind):
    """The server says no to the sender, to the DATA command or to the message it was given: it did not take the message, so this
    is a message not sent, of the kind its code tells (a 4xx code may pass at another try)."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.answers.update(answers)
    with pytest.raises(MailerError) as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert str(raised.value) == text and raised.value.kind == kind and not isinstance(raised.value, HandOverUnconfirmed)


@pytest.mark.parametrize("step, error", [("body", smtplib.SMTPServerDisconnected("Connection unexpectedly closed")),
                                         ("reply", TimeoutError("timed out")), ("reply", smtplib.SMTPServerDisconnected("closed"))])
def test_a_failure_after_the_message_was_given_leaves_the_hand_over_unconfirmed(smtp, monkeypatch, step, error):
    """Ruling S5 of the review round of Task 44. The connection fails while the message is given, or before the server's answer
    to it: nobody knows whether the message left. That is an error of its own, HandOverUnconfirmed, which carries the record of the
    message with its id, marked as not confirmed, so that the caller counts the question as asked and a reply that names it finds
    its case; its text names the class and the step, no address."""
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.fail[step] = error
    with pytest.raises(HandOverUnconfirmed) as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com", clock=lambda: SENT).send(MESSAGE)
    record = raised.value.record
    assert str(raised.value) == f"{type(error).__name__}: no confirmation that smtp.example.com:587 took the message"
    assert raised.value.kind == "unconfirmed" and isinstance(raised.value, MailerError) and raised.value.__suppress_context__
    assert (record.to, record.subject, record.sent_at, record.template, record.unconfirmed) == (
        MESSAGE.to, MESSAGE.subject, "2026-11-20T16:00:00", "quantity_unit", True)
    assert given(smtp, 0)["Message-ID"] == f"<{record.message_id}@example.com>" and json.loads(record.model_dump_json())["unconfirmed"] is True


def test_the_smtp_mailer_needs_its_password_and_a_closing_error_does_not_unsend(smtp, monkeypatch):
    monkeypatch.delenv("AID_MAIL_PASSWORD", raising=False)
    with pytest.raises(MailerError, match="^KeyError: the environment variable AID_MAIL_PASSWORD is not set$") as raised:
        SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE)
    assert smtp.calls == [] and raised.value.kind == "no_password"            # no connection without the password
    monkeypatch.setenv("AID_MAIL_PASSWORD", SECRET)
    smtp.fail["quit"] = smtplib.SMTPResponseException(421, b"closing")
    assert SmtpMailer("smtp.example.com", 587, "aid@example.com").send(MESSAGE).to == MESSAGE.to   # the message went before the error


def test_a_file_mailer_says_whether_it_writes_files_by_choice(tmp_path):
    """Ruling S4 of the review round of Task 44: a file mailer given for want of a route carries why (`unrouted`), and one that
    writes files by the owner's choice carries None; it writes its file either way, since the run's own messages are not changed."""
    chosen, wanting = FileMailer(tmp_path / "a"), FileMailer(tmp_path / "b", unrouted="no_mailbox")
    assert (chosen.unrouted, wanting.unrouted) == (None, "no_mailbox")
    assert (tmp_path / "b" / f"{wanting.send(MESSAGE).message_id}.eml").exists()


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


# --- a reply belongs to its question (Task 44) ---

MONDAY = datetime(2026, 11, 23, 16, 0)               # the Monday after SENT: a second question's wait ends on Thursday 26 November at 16:00


def naming(path: Path, in_reply_to: str | None = None, references: str | None = None, subject: str = "Re: Vraag over orderbevestiging PO 250101",
           sender: str = "sales@supplier.example.com") -> Path:
    """A made-up e-mail to the aid's mailbox that names the messages it answers in In-Reply-To and References, where they are given."""
    named = "".join(f"{name}: {value}\n" for name, value in (("In-Reply-To", in_reply_to), ("References", references)) if value)
    path.write_bytes(f"From: {sender}\nTo: aid@example.com\nSubject: {subject}\n{named}\nA made-up message.\n".encode())
    return path


def delivery_report(path: Path, message_id: str, returned: str = "text/rfc822-headers") -> Path:
    """A made-up delivery report in the standard form (RFC 3464), as a receiving mail server sends it: multipart/report with a text for
    a person, the delivery status, and the headers of the returned message (text/rfc822-headers) or that message itself
    (message/rfc822). It has no In-Reply-To: the Message-ID of the returned message stands in its third part only."""
    headers = f"From: aid@example.com\nTo: sales@supplier.example.com\nSubject: Vraag over orderbevestiging PO 250101\nMessage-ID: {message_id}\n"
    third = headers if returned == "text/rfc822-headers" else f"{headers}\nA made-up question.\n"
    path.write_bytes(("From: Mail Delivery System <MAILER-DAEMON@mx.supplier.example.com>\nTo: aid@example.com\n"
                      "Subject: Undelivered Mail Returned to Sender\nMIME-Version: 1.0\n"
                      'Content-Type: multipart/report; report-type=delivery-status; boundary="b1"\n\n'
                      "--b1\nContent-Type: text/plain; charset=us-ascii\n\n<sales@supplier.example.com>: user unknown\n\n"
                      "--b1\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; mx.supplier.example.com\n\n"
                      "Final-Recipient: rfc822; sales@supplier.example.com\nAction: failed\nStatus: 5.1.1\n\n"
                      f"--b1\nContent-Type: {returned}\n\n{third}\n--b1--\n").encode())
    return path


def waiting(case: Case) -> list[str]:
    """The message ids of the open questions of the case, in the order they were sent."""
    return [question.message_id for question in open_questions(case.root / "messages", 3)]


def came_back(case: Case) -> list[str]:
    """The message ids of the questions of the case that bounced and were not shown yet, in the order they were sent."""
    return [question.message_id for question in bounced_questions(case.root / "messages", 3)]


def test_a_reply_to_an_earlier_question_leaves_the_later_one_waiting(tmp_path):
    """Two questions, the first answered by a reply whose In-Reply-To names it: the second has no reply of its own, so the case waits
    for it, and its wait ends at its own deadline. A reply to the second then ends the wait."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    assert waiting(case) == ["m1", "m2"]
    file_reply(case, naming(tmp_path / "first.eml", "<m1@example.com>"))
    assert waiting(case) == ["m2"]
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 26, 16, 0), 3) == ([], [])
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 26, 16, 1), 3) == ([EndedWait(case.case_id, "m2", False)], [])
    file_reply(case, naming(tmp_path / "second.eml", "<m2@example.com>", references="<m1@example.com> <m2@example.com>"))
    assert waiting(case) == [] and pending_asks(tmp_path / "cases", datetime(2026, 12, 14, 9, 0), 3) == []


def test_a_reply_belongs_to_the_question_of_its_in_reply_to_else_to_the_last_of_its_references(tmp_path):
    """named_ids gives the message ids an e-mail names in the order they count: those of In-Reply-To, then those of References from
    the last back to the first. A reply whose In-Reply-To names no question of the case belongs to the last question its References
    names, and one that names only another message belongs to no question of the case."""
    reply = naming(tmp_path / "r.eml", "<other@example.org>", references="<m2@example.com>\n <m1@example.com> <later@example.org>")
    assert named_ids(reply) == ["other", "later", "m1", "m2"]
    assert named_ids(naming(tmp_path / "plain.eml")) == [] and named_ids(naming(tmp_path / "bare.eml", "m1@example.com")) == []
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    file_reply(case, naming(tmp_path / "elsewhere.eml", "<other@example.org>", references="<older@example.org>"))
    assert waiting(case) == ["m1", "m2"]
    file_reply(case, reply)
    assert waiting(case) == ["m2"]
    file_reply(case, naming(tmp_path / "both.eml", "<m2@example.com>", references="<m1@example.com>"))     # the In-Reply-To counts first
    assert waiting(case) == []


def test_a_reply_without_headers_answers_the_latest_question(tmp_path):
    """An e-mail that names no message at all (one put into the case by hand) answers the latest question sent before it was filed;
    an earlier question keeps waiting for a reply of its own, and its wait ends at its own deadline."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    file_reply(case, at_time(write_eml(tmp_path / "by_hand.eml", subject="Re: Vraag over orderbevestiging PO 250101"), MONDAY + timedelta(hours=1)))
    assert waiting(case) == ["m1"]
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 25, 16, 1), 3) == ([EndedWait(case.case_id, "m1", False)], [])
    one = sent_case(tmp_path, "250102", (SENT, "quantity_unit"))
    file_reply(one, at_time(write_eml(tmp_path / "by_hand.eml", subject="Re: Vraag over orderbevestiging PO 250102"), MONDAY))
    assert waiting(one) == [] and one.case_id not in pending_asks(tmp_path / "cases", datetime(2026, 12, 14, 9, 0), 3)


def test_a_reply_that_names_an_escalated_question_ends_no_wait(tmp_path):
    """The first question was escalated and a second one sent after it: a late reply to the first is no answer to the second, whose
    wait goes on to its own deadline. An escalation covers the question it names and the earlier questions whose own wait had ended
    by then (ruling S20): a question sent earlier whose wait had not ended stays open, and comes back when its own wait ends."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    record_escalation(case, "no answer in 3 working days", MONDAY - timedelta(hours=1), "m1")
    assert waiting(case) == ["m2"]
    file_reply(case, naming(tmp_path / "late.eml", "<m1@example.com>"))
    assert waiting(case) == ["m2"]
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 26, 16, 1), 3) == ([EndedWait(case.case_id, "m2", False)], [])
    earlier = sent_case(tmp_path, "250102", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    record_escalation(earlier, "message bounced", MONDAY + timedelta(minutes=5), "m2")           # Monday: m1's wait runs to Wednesday 16:00
    assert waiting(earlier) == ["m1"]
    assert [wait for wait in ended_waits(tmp_path / "cases", datetime(2026, 11, 25, 16, 0), 3)[0] if wait.case_id == earlier.case_id] == []
    assert [wait for wait in ended_waits(tmp_path / "cases", datetime(2026, 11, 25, 16, 1), 3)[0] if wait.case_id == earlier.case_id] == [
        EndedWait(earlier.case_id, "m1", False)]
    silent = sent_case(tmp_path, "250103", (SENT, "quantity_unit"), (MONDAY, "missing_info"))        # two silent questions: one escalation
    record_escalation(silent, "no answer in 3 working days", datetime(2026, 11, 26, 16, 1), "m2")   # of the latest covers both
    assert waiting(silent) == [] and silent.case_id not in pending_asks(tmp_path / "cases", datetime(2026, 12, 14, 9, 0), 3)


def test_a_bounce_of_an_earlier_question_does_not_bounce_the_next(tmp_path):
    """A bounce belongs to the question its In-Reply-To names. The first question bounces and is escalated; a second question, sent
    to the corrected address, has no bounce of its own: it waits its three working days, and is not given as bounced a minute later."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    bounce = naming(tmp_path / "bounce.eml", "<m1@example.com>", subject="Undeliverable: Vraag over orderbevestiging PO 250101", sender="postmaster@example.com")
    assert file_reply(case, bounce).parent.name == "bounces" and came_back(case) == ["m1"]
    assert waiting(case) == [] and ended_waits(tmp_path / "cases", SENT + timedelta(minutes=5), 3) == ([EndedWait(case.case_id, "m1", True)], [])
    record_escalation(case, "message bounced", SENT + timedelta(minutes=5), "m1")
    record_sent(case, SentRecord(message_id="m2", to=["sales@supplier.example.com"], subject="s", sent_at=MONDAY.isoformat(), template="quantity_unit"))
    assert came_back(case) == [] and waiting(case) == ["m2"]
    assert ended_waits(tmp_path / "cases", MONDAY + timedelta(minutes=1), 3) == ([], [])
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 26, 16, 1), 3) == ([EndedWait(case.case_id, "m2", False)], [])
    # of two questions without a reply, the one that bounced comes back at once and the other waits on
    two = sent_case(tmp_path, "250102", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    file_reply(two, bounce)
    assert [wait for wait in ended_waits(tmp_path / "cases", MONDAY + timedelta(minutes=1), 3)[0] if wait.case_id == two.case_id] == [
        EndedWait(two.case_id, "m1", True)]
    record_escalation(two, "message bounced", MONDAY + timedelta(minutes=1), "m1")
    assert waiting(two) == ["m2"] and two.case_id not in pending_asks(tmp_path / "cases", MONDAY + timedelta(minutes=2), 3)
    # both questions of one close bounce (a wrong address): each is given once, in the order sent, and none again once it is shown
    both = sent_case(tmp_path, "250103", (MONDAY, "quantity_unit"), (MONDAY, "missing_info"))
    for n in (1, 2):
        file_reply(both, naming(tmp_path / f"bounce{n}.eml", f"<m{n}@example.com>", subject="Undeliverable: Vraag", sender="postmaster@example.com"))
    assert came_back(both) == ["m1", "m2"]
    assert [wait for wait in ended_waits(tmp_path / "cases", MONDAY + timedelta(minutes=1), 3)[0] if wait.case_id == both.case_id] == [
        EndedWait(both.case_id, "m1", True), EndedWait(both.case_id, "m2", True)]
    assert pending_asks(tmp_path / "cases", MONDAY + timedelta(minutes=1), 3).count(both.case_id) == 1
    record_escalation(both, "message bounced", MONDAY + timedelta(minutes=1), "m1")
    assert came_back(both) == ["m2"]
    record_escalation(both, "message bounced", MONDAY + timedelta(minutes=1), "m2")
    assert came_back(both) == [] and waiting(both) == [] and both.case_id not in pending_asks(tmp_path / "cases", datetime(2026, 12, 14, 9, 0), 3)


@pytest.mark.parametrize("returned", ["text/rfc822-headers", "message/rfc822"])
def test_a_delivery_report_names_the_message_it_returns(tmp_path, returned):
    """A delivery report in the standard form has no In-Reply-To: it names its question by the Message-ID of the message it returns,
    read from the part that holds the returned headers or the returned message. It is the bounce of that question and of no other."""
    report = delivery_report(tmp_path / "dsn.eml", "<m1@example.com>", returned)
    assert named_ids(report) == ["m1"] and is_bounce(report)
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (MONDAY, "missing_info"))
    assert file_reply(case, report) == case.root / "messages" / "bounces" / "dsn.eml"
    assert came_back(case) == ["m1"] and waiting(case) == ["m2"]
    assert ended_waits(tmp_path / "cases", MONDAY + timedelta(minutes=1), 3) == ([EndedWait(case.case_id, "m1", True)], [])
    other = sent_case(tmp_path, "250102", (SENT, "quantity_unit"))
    file_reply(other, delivery_report(tmp_path / "elsewhere.eml", "<0123abcd@example.org>", returned))    # the report of another message
    assert came_back(other) == [] and waiting(other) == ["m1"]
    # a reply that carries a message as an attachment is no delivery report: the attachment's Message-ID is not what it names
    forwarded = EmailMessage()
    forwarded["From"], forwarded["To"], forwarded["Subject"] = "sales@supplier.example.com", "aid@example.com", "Fwd: uw vraag"
    forwarded.set_content("Zie de bijlage.")
    attached = EmailMessage()
    attached["Message-ID"], attached["Subject"] = "<m1@example.com>", "Vraag over orderbevestiging PO 250101"
    attached.set_content("A made-up question.")
    forwarded.add_attachment(attached)
    (tmp_path / "forwarded.eml").write_bytes(bytes(forwarded))
    assert named_ids(tmp_path / "forwarded.eml") == []
    receipt = tmp_path / "receipt.eml"                         # a read receipt is a report too, of another type: it returns nothing that counts
    receipt.write_bytes(report.read_bytes().replace(b"report-type=delivery-status", b"report-type=disposition-notification"))
    assert named_ids(receipt) == []


@pytest.mark.parametrize("subject, expected", [
    ("Re: Vraag over orderbevestiging PO 250117: regel 3 kon niet worden bezorgd", False),
    ("Re: PO 250101 - the pallet was undeliverable on Monday", False),
    ("Antw: Vraag over orderbevestiging PO 250101 (eerdere mail niet bezorgd?)", False),
    ("AW: Bestellung 250101, Lieferung unzustellbar an Tor 3", False),
    ("Undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("FW: Undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("RE: Fwd:Onbestelbaar: Vraag over orderbevestiging PO 250101", True),
    ("  Mail delivery failed: returning message to sender", True),
    ("Uw bericht kon niet worden bezorgd", True),
    ("Nachricht wurde nicht zugestellt", True),
])
def test_the_words_of_a_bounce_stand_at_the_start_of_the_subject(tmp_path, subject, expected):
    """The words of a bounce count at the start of the subject, after any reply or forward prefixes, as those of an automatic reply
    do: a real reply that mentions them further on is a reply, and is filed as one. The sender of a mail system still makes a bounce."""
    path = write_eml(tmp_path / "m.eml", subject=subject)
    assert is_bounce(path) is expected
    case = open_case(tmp_path / "cases", "confirmation", [], "250101", SENT)
    assert file_reply(case, path).parent.name == ("bounces" if expected else "replies")
    assert is_bounce(write_eml(tmp_path / "daemon.eml", subject=subject, sender="Mail Delivery System <MAILER-DAEMON@mail.example.com>"))


# --- the review round of Task 44 (rulings S11 to S25) ---

def report(path: Path, message_id: str, action: str, subject: str = "Undelivered Mail Returned to Sender",
           sender: str = "Mail Delivery System <MAILER-DAEMON@mx.supplier.example.com>", returned: str | None = None, extra: str = "") -> Path:
    """A made-up delivery report in the standard form whose delivery-status part gives this Action, under this subject and from
    this sender; `returned` is the third part when it is not the headers of the question (its content type and its text)."""
    kind, third = returned or ("text/rfc822-headers", f"From: aid@example.com\nSubject: Vraag\nMessage-ID: {message_id}\n")
    status = f"Final-Recipient: rfc822; sales@supplier.example.com\nAction: {action}\nStatus: 5.1.1\n" if action else "No fields here.\n"
    path.write_bytes((f"From: {sender}\nTo: aid@example.com\nSubject: {subject}\n{extra}MIME-Version: 1.0\n"
                      'Content-Type: multipart/report; report-type=delivery-status; boundary="b1"\n\n'
                      "--b1\nContent-Type: text/plain; charset=us-ascii\n\nThis is the mail system.\n\n"
                      f"--b1\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; mx.supplier.example.com\n\n{status}\n"
                      f"--b1\nContent-Type: {kind}\n\n{third}\n--b1--\n").encode())
    return path


def test_a_bounce_of_an_answered_question_changes_nothing(tmp_path):
    """Ruling S11: a question that has a reply of its own is answered. A non-delivery notice of it (one of several recipients, the
    copy address) is filed, and the question is neither outstanding nor given as bounced, before the reply or after it."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    file_reply(case, naming(tmp_path / "reply.eml", "<m1@example.com>"))
    bounce = naming(tmp_path / "ndr.eml", "<m1@example.com>", subject="Undeliverable: Vraag", sender="postmaster@example.com")
    assert file_reply(case, bounce).parent.name == "bounces"
    assert (waiting(case), came_back(case)) == ([], []) and ended_waits(tmp_path / "cases", datetime(2026, 12, 14, 9, 0), 3) == ([], [])
    first = sent_case(tmp_path, "250102", (SENT, "quantity_unit"))               # the bounce first, then the reply
    file_reply(first, bounce)
    assert came_back(first) == ["m1"]
    file_reply(first, naming(tmp_path / "reply.eml", "<m1@example.com>"))
    assert (waiting(first), came_back(first)) == ([], []) and pending_asks(tmp_path / "cases", datetime(2026, 12, 14, 9, 0), 3) == []


@pytest.mark.parametrize("action, subject, sender, extra, kind", [
    ("failed", "Undelivered Mail Returned to Sender", "Mail Delivery System <MAILER-DAEMON@mx.example.org>", "", "bounce"),
    ("failed", "Bericht van het mailsysteem", "Mailsysteem <systeem@mx.example.org>", "", "bounce"),             # no bounce word at all
    ("Failed", "Re: Vraag over orderbevestiging PO 250101", "sales@supplier.example.com", "", "bounce"),
    ("delayed", "Delayed Mail (still being retried)", "Mail Delivery System <MAILER-DAEMON@mx.example.org>", "", "auto"),
    ("delayed", "Delivery delayed: Vraag over orderbevestiging PO 250101", "Microsoft Outlook <MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@example.com>", "", "auto"),
    ("delayed", "Delivery delayed: Vraag over orderbevestiging PO 250101", "postmaster@example.org", "Auto-Submitted: auto-replied\n", "auto"),
    ("delivered", "Successful Mail Delivery Report", "Mail Delivery System <MAILER-DAEMON@mx.example.org>", "", "auto"),
    ("relayed", "Relayed: Vraag over orderbevestiging PO 250101", "postmaster@example.org", "", "auto"),
    ("expanded", "Delivery Status Notification (Success)", "postmaster@example.org", "", "auto"),
    ("", "Undelivered Mail Returned to Sender", "Mail Delivery System <MAILER-DAEMON@mx.example.org>", "", "bounce"),   # no Action to read:
    ("", "Bericht van het mailsysteem", "Mailsysteem <systeem@mx.example.org>", "", "reply"),                           # by subject and sender
    ("delivered; failed", "Bericht van het mailsysteem", "Mailsysteem <systeem@mx.example.org>", "", "reply"),          # no known Action
])
def test_a_delivery_report_says_itself_what_it_is(tmp_path, action, subject, sender, extra, kind):
    """Ruling S13: for a delivery report in the standard form the Action of its delivery-status part decides, whatever its subject or
    sender: "failed" is a bounce of the message it returns; "delayed", "delivered", "relayed" and "expanded" make it an automatic
    message, which ends no wait and answers nothing. A report whose Action cannot be read is judged by its subject and sender."""
    path = report(tmp_path / "report.eml", "<m1@example.com>", action, subject, sender, extra=extra)
    assert (kind_of(path), is_bounce(path), is_auto_reply(path)) == (kind, kind == "bounce", kind == "auto")
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    assert file_reply(case, path).parent.name == {"bounce": "bounces", "auto": "auto", "reply": "replies"}[kind]
    assert came_back(case) == (["m1"] if kind == "bounce" else []) and waiting(case) == (["m1"] if kind == "auto" else [])
    if kind == "auto":                                                       # it ends no wait: the question comes back after its own wait
        assert ended_waits(tmp_path / "cases", datetime(2026, 11, 25, 16, 1), 3) == ([EndedWait(case.case_id, "m1", False)], [])


def test_a_report_with_a_failed_and_a_delayed_recipient_is_a_bounce(tmp_path):
    """A report on two recipients, one delayed and one failed: the failure makes it a bounce."""
    path = report(tmp_path / "report.eml", "<m1@example.com>", "delayed\nStatus: 4.4.1\n\nFinal-Recipient: rfc822; inkoop@supplier.example.com\nAction: failed",
                  "Bericht van het mailsysteem", "Mailsysteem <systeem@mx.example.org>")
    assert kind_of(path) == "bounce"


def test_a_file_without_ids_answers_the_latest_question_sent_before_it_was_filed(tmp_path):
    """Ruling S16: an e-mail that names no message (one put into replies/ by hand) answers the latest question sent before it was
    filed, by the time of its entry in the audit's answers, else by the file's own time, and never a question sent after it."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    saturday = SENT + timedelta(days=1)                                      # put there on Saturday: after the first question
    file_reply(case, at_time(write_eml(tmp_path / "by_hand.eml", subject="Uw order"), saturday))
    assert waiting(case) == []
    record_sent(case, SentRecord(message_id="m2", to=["sales@supplier.example.com"], subject="s", sent_at=MONDAY.isoformat(), template="missing_info"))
    assert waiting(case) == ["m2"]                                           # the question of Monday waits on its own
    assert ended_waits(tmp_path / "cases", datetime(2026, 11, 26, 16, 1), 3) == ([EndedWait(case.case_id, "m2", False)], [])
    early = sent_case(tmp_path, "250102", (MONDAY, "quantity_unit"))
    before = file_reply(early, at_time(write_eml(tmp_path / "by_hand.eml", subject="Uw order"), saturday))    # before any question: it answers none
    assert waiting(early) == ["m1"]
    (early.root / "audit.json").write_text(json.dumps({"answers": [{"file": before.name, "filed_under": "replies",
                                                                    "at": (MONDAY + timedelta(hours=1)).isoformat()}]}), encoding="utf-8")
    assert waiting(early) == []                                              # the audit's time of the filing counts before the file's own
    same = sent_case(tmp_path, "250103", (MONDAY, "quantity_unit"))          # filed in the very second the question was sent: the times
    file_reply(same, at_time(write_eml(tmp_path / "by_hand.eml", subject="Uw order"), MONDAY))      # are kept to the second, so that
    assert waiting(same) == ["m1"]                                           # question counts as sent after it, and is not answered


def test_a_reply_that_cannot_be_read_is_no_answer(tmp_path, monkeypatch):
    """Ruling S17: a filed reply that cannot be read at that moment belongs to no question. Its case is among those that cannot be
    judged at that pass, the other cases go on, and once the file reads again the reply is the answer it is."""
    import aid.messaging

    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    held = file_reply(case, naming(tmp_path / "reply.eml", "<m1@example.com>"))
    other = sent_case(tmp_path, "250102", (SENT, "quantity_unit"))
    read = aid.messaging.named_ids

    def locked(eml):
        if Path(eml) == held:
            raise PermissionError(13, "made-up: the file is in use by another program")
        return read(eml)
    monkeypatch.setattr(aid.messaging, "named_ids", locked)
    now = datetime(2026, 12, 14, 9, 0)
    assert ended_waits(tmp_path / "cases", now, 3) == ([EndedWait(other.case_id, "m1", False)], [(case.case_id, "PermissionError")])
    with pytest.raises(OSError):
        open_questions(case.root / "messages", 3)
    monkeypatch.setattr(aid.messaging, "named_ids", read)
    assert waiting(case) == [] and ended_waits(tmp_path / "cases", now, 3) == ([EndedWait(other.case_id, "m1", False)], [])


def test_a_delivery_report_names_the_message_it_returns_and_no_other(tmp_path):
    """Ruling S18: only the report's own parts are read, and of a returned message only its own top headers: a report that returns
    an e-mail which carries the question as an attachment names that e-mail, and nothing of the question."""
    question = "From: aid@example.com\nSubject: Vraag\nMessage-ID: <m1@example.com>\n\nA made-up question.\n"
    carrier = ("From: buyer@example.com\nTo: wrong@example.org\nSubject: FW: Vraag\nMessage-ID: <fwd123@example.com>\nMIME-Version: 1.0\n"
               'Content-Type: multipart/mixed; boundary="f1"\n\n--f1\nContent-Type: text/plain\n\nZie bijlage.\n\n'
               f"--f1\nContent-Type: message/rfc822\n\n{question}\n--f1--\n")
    path = report(tmp_path / "report.eml", "", "failed", returned=("message/rfc822", carrier))
    assert named_ids(path) == ["fwd123"]
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    file_reply(case, path)
    assert (waiting(case), came_back(case)) == (["m1"], [])
    # a report wrapped inside another part is no part of the report itself
    nested = report(tmp_path / "nested.eml", "", "failed", returned=(
        'multipart/mixed; boundary="n1"', "--n1\nContent-Type: text/rfc822-headers\n\nMessage-ID: <m1@example.com>\n\n--n1--\n"))
    assert named_ids(nested) == []


def test_the_first_id_that_is_a_question_of_the_case_decides(tmp_path):
    """Ruling S19: a reply whose In-Reply-To names a question whose wait was over (sent before the last escalation) ends no wait,
    also when its References name a later question that is still out: that one keeps waiting and comes back on its own."""
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"), (SENT, "missing_info"))
    record_escalation(case, "no answer in 3 working days", datetime(2026, 11, 25, 16, 1), "m2")       # covers m1 as well
    third = datetime(2026, 11, 26, 9, 0)
    record_sent(case, SentRecord(message_id="m3", to=["sales@supplier.example.com"], subject="s", sent_at=third.isoformat(), template="missing_info"))
    assert waiting(case) == ["m3"]
    file_reply(case, naming(tmp_path / "late.eml", "<m1@example.com>", references="<m3@example.com> <m1@example.com>"))
    assert waiting(case) == ["m3"]
    assert ended_waits(tmp_path / "cases", datetime(2026, 12, 1, 9, 1), 3) == ([EndedWait(case.case_id, "m3", False)], [])
    file_reply(case, naming(tmp_path / "late2.eml", "<m2@example.com>", references="<m3@example.com>"))    # the escalated question itself
    assert waiting(case) == ["m3"]


def test_an_e_mail_from_the_aids_own_side_is_the_buyers_own_message(tmp_path):
    """Ruling S21: an e-mail whose sender is the buyer's address, the aid's address or the address of the mailbox is no answer of the
    supplier (the buyer is in copy, so his reply to all names the question): it is filed with its case under own/, whatever its
    subject, and ends no wait. Any other sender counts as before."""
    own = ["buyer@example.com", "aid@example.com", None, "Inkoop@Example.com"]
    mine = naming(tmp_path / "mine.eml", "<m1@example.com>", sender="De Inkoper <BUYER@example.com>")
    assert kind_of(mine, own) == "own" and kind_of(mine) == "reply" and kind_of(naming(tmp_path / "x.eml", "<m1@example.com>", sender="inkoop@example.com"), own) == "own"
    assert kind_of(naming(tmp_path / "theirs.eml", "<m1@example.com>", sender="buyer@supplier.example.com"), own) == "reply"
    out_of_office = write_eml(tmp_path / "ooo.eml", subject="Automatisch antwoord: afwezig", sender="buyer@example.com", auto_submitted="auto-replied")
    assert kind_of(out_of_office, own) == "own" and kind_of(out_of_office) == "auto"
    case = sent_case(tmp_path, "250101", (SENT, "quantity_unit"))
    assert file_reply(case, mine, own) == case.root / "messages" / "own" / "mine.eml"
    assert waiting(case) == ["m1"] and ended_waits(tmp_path / "cases", datetime(2026, 11, 25, 16, 1), 3) == ([EndedWait(case.case_id, "m1", False)], [])


@pytest.mark.parametrize("subject, expected", [
    ("[EXTERN] Undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("[EXTERNAL] Onbestelbaar: Vraag over orderbevestiging PO 250101", True),
    ("EXTERN: Undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("[SPAM?] Undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("***SPAM*** Mail delivery failed: returning message to sender", True),
    ("[Postmaster] Undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("TR: Undeliverable: Vraag", True), ("WG: Unzustellbar: Vraag", True),
    ("Niet te bezorgen: Vraag over orderbevestiging PO 250101", True),
    ("Nicht zustellbar: Vraag over orderbevestiging PO 250101", True),
    ("Returned mail: see transcript for details", True),
    ("Failure Notice", True),
    ("Message undeliverable: Vraag over orderbevestiging PO 250101", True),
    ("Mail delivery failed", True),
    ("Delivery Status Notification (Failure)", True),
    ("Delivery Status Notification (Delay)", False),
    ("Delivery Status Notification (Success)", False),
    ("Re: Vraag over orderbevestiging PO 250101", False),
    ("WG: Vraag over orderbevestiging PO 250101", False),
    ("[EXTERN] RE: Vraag over orderbevestiging PO 250117: regel 3 kon niet worden bezorgd", False),
    ("EXTERN: Re: Vraag over orderbevestiging PO 250101 (eerdere mail niet bezorgd?)", False),
    ("Re: [ticket 4411] Vraag over orderbevestiging PO 250101, failure notice of our printer", False),
    ("PO 250101: Undeliverable goods, please advise", False),
])
def test_the_words_of_a_bounce_stand_after_what_mail_programs_put_before_them(tmp_path, subject, expected):
    """Ruling S22: before the words of a bounce may stand the reply and forward prefixes (WG and TR among them), tags in square
    brackets, tags between asterisks and one word in capitals with a colon, as a mail gateway that marks outside mail puts them; a
    delay or a success notice is no bounce by its subject; and a subject that begins with the question's own subject is a reply."""
    assert is_bounce(write_eml(tmp_path / "m.eml", subject=subject)) is expected


def test_a_pass_reads_the_replies_of_the_cases_that_wait_and_of_no_other(tmp_path, monkeypatch):
    """Ruling S25: ended_waits asks `waiting` about a case before it reads anything of it, and reads no file of a case that does
    not wait; without `waiting` every case is judged, as before."""
    import aid.messaging

    cases = [sent_case(tmp_path, f"25010{n}", (SENT, "quantity_unit")) for n in (1, 2, 3)]
    for case in cases[:2]:
        file_reply(case, naming(tmp_path / "reply.eml", "<m1@example.com>"))
    read, seen = aid.messaging.named_ids, []
    monkeypatch.setattr(aid.messaging, "named_ids", lambda eml: (seen.append(Path(eml).parents[2].name), read(eml))[1])
    now = datetime(2026, 12, 14, 9, 0)
    assert ended_waits(tmp_path / "cases", now, 3, waiting=lambda case_id: case_id != cases[0].case_id) == ([EndedWait(cases[2].case_id, "m1", False)], [])
    assert seen == [cases[1].case_id]
    assert ended_waits(tmp_path / "cases", now, 3)[0] == [EndedWait(cases[2].case_id, "m1", False)] and seen.count(cases[0].case_id) == 1
