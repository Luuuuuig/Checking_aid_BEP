"""Sending through the work mailbox: MicrosoftMailer, a mailer of aid/messaging (the Mailer protocol), sends the supplier questions,
the re-approval requests and the digest from the mailbox of the user's own Microsoft sign-in (aid/connections/microsoft.py), or
from the mailbox of settings.mailbox, through Microsoft Graph. The From is the mailbox; for the account's own mailbox, without a
sender given, it is the account signed in when the message is sent, so that a sign-in made after the runner started counts. The
token is asked with mail_scopes(mailbox) of aid/connections/microsoft.py: another mailbox needs the shared scopes.
AutoMailer is the mailer of send_via "auto" while a folder of the mailbox is read: it decides at each message, sending through the
mailbox while an account is signed in, which it reads from the token cache without the network, and through the other mailer (SMTP
or files) otherwise. An account whose sign-in has lapsed stays in the cache: its messages are not sent (SignInLapsed, which the
pipeline records as not sent, for the buyer to send) and no other way is tried, since a reply to another sender would never reach
the folder that is read, and so never its case. Only after a sign-out (Afmelden) do the messages go through the other mailer.

The e-mail is the one the other mailers send: _email of aid/messaging builds it, with the aid's Message-ID, <message id@the sender's
domain>, the header Auto-Submitted and the headers X-Aid-Template and X-Aid-Case, written as MIME with CRLF line ends. Graph takes
MIME as the base64 of the e-mail with the Content-Type text/plain, as Microsoft documents for MIME
(https://learn.microsoft.com/en-us/graph/outlook-send-mime-message).

Whether Exchange keeps that Message-ID. A reply finds its case by the Message-ID that its In-Reply-To or References header names
(file_answer of aid/pipeline.py), so the Message-ID the supplier receives must be one the case has recorded. Microsoft's
documentation, read on 8 November 2026, does not say that Exchange keeps the Message-ID of a MIME message sent through Graph:
- the page of sendMail (https://learn.microsoft.com/en-us/graph/api/user-sendmail) says to give the Internet message headers and the
  MIME content in base64 and that the message is saved in Sent Items, and nothing about the Message-ID;
- the page on sending MIME (https://learn.microsoft.com/en-us/graph/outlook-send-mime-message) shows a Message-ID among the headers
  of its example, and does not say whether it is kept;
- the overview of the send process (https://learn.microsoft.com/en-us/graph/outlook-things-to-know-about-send-mail) says that
  Exchange parses the MIME into the properties of a new draft and that its transport copies the MIME content "more or less intact";
- the message resource (https://learn.microsoft.com/en-us/graph/api/resources/message) defines internetMessageId as "The message ID
  in the format specified by RFC2822", and the page on creating a message
  (https://learn.microsoft.com/en-us/graph/api/user-post-messages) answers with the draft, its internetMessageId included.
The mailer therefore does not call sendMail. It creates the message from the MIME as a draft (POST /{me | users/<mailbox>}/messages,
answered with the draft), reads the draft's id and internetMessageId, and sends the draft (POST .../messages/<id>/send, answered
202), which Exchange then saves in Sent Items. The SentRecord keeps the internetMessageId (internet_message_id), and its message_id
is the part of that id before the "@": the aid's own message id when Exchange kept the aid's Message-ID, else that part of the id
Exchange gave, so that a reply naming the id the supplier received finds its case through the matching of aid/pipeline.py as it
is. A draft without an internetMessageId keeps the aid's own id. Creating a draft takes the delegated permission Mail.ReadWrite
besides Mail.Send (the page on creating a message says so), which MAIL_SCOPES asks for. A draft whose sending fails stays in the
Drafts folder.

Errors. A lapsed sign-in, an answer of Graph other than success and a request that fails are a MailerError that names the status
code ("HTTP 403"), the class of the error or the lapsed sign-in, and the step it stopped at; never a token, an address or Graph's
own text, which the pipeline would keep in the audit. A lapsed sign-in (no token to be had, or a 401) is SignInLapsed, a MailerError
of its own, so that the runner writes the digest as a file instead; so is a store of the sign-in that this process may not use
(StoreRefused of aid/connections/secrets.py), by a fixed text that names neither its folder nor its kind. A Microsoft that cannot
be reached for the token is a MailerError with the class of the error.
"""
import base64
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

import requests

from aid.connections.microsoft import (GRAPH, GRAPH_TIMEOUT, SIGN_IN_AGAIN, MicrosoftAccount, MicrosoftUnreachable, SignInNeeded, mail_scopes,
                                       owner_path)
from aid.connections.secrets import StoreRefused
from aid.messaging import Mailer, MailerError, Message, SentRecord, _email, _sent

_INTERNET_ID = re.compile(r"<([^<>@\s]+)@[^<>\s]*>")      # a Message-ID; its first group is the part before the "@"
STORE_REFUSED = "StoreRefused: this process may not use the store of the Microsoft sign-in on this PC (the manual's section 5 says why)"


class SignInLapsed(MailerError):
    """A message not sent because the Microsoft sign-in has lapsed (see the module docstring)."""


class MicrosoftMailer:
    """Sends each message through Graph from the mailbox (None: the signed-in account's own), From the sender (when none is given:
    the mailbox, or the account signed in at the send), at the clock's time (see the module docstring)."""
    name = "microsoft"

    def __init__(self, account: MicrosoftAccount, mailbox: str | None, sender: str, clock: Callable[[], datetime], session=None):
        self.account, self.mailbox, self.sender, self.clock, self.session = account, mailbox, sender, clock, session

    def send(self, msg: Message) -> SentRecord:
        try:
            token = self.account.token(mail_scopes(self.mailbox))
        except SignInNeeded:
            raise SignInLapsed(f"SignInNeeded: {SIGN_IN_AGAIN}") from None
        except StoreRefused:
            raise SignInLapsed(STORE_REFUSED) from None
        except MicrosoftUnreachable as error:
            raise MailerError(f"{error}: could not get a token from Microsoft") from None
        message_id, sent_at = uuid.uuid4().hex, self.clock()
        mail = _email(msg, self.sender or self.mailbox or self.account.account() or "", sent_at, message_id)
        mime = mail.as_bytes(policy=mail.policy.clone(linesep="\r\n"))
        base, authorization = f"{GRAPH}/{owner_path(self.mailbox)}", {"Authorization": f"Bearer {token}"}
        draft = self._post(f"{base}/messages", authorization | {"Content-Type": "text/plain"}, base64.b64encode(mime),
                           "create the draft")
        try:
            created = draft.json()
        except ValueError:
            created = None
        if not isinstance(created, dict) or not isinstance(created.get("id"), str):
            raise MailerError("could not create the draft: the answer of Graph holds no id")
        self._post(f"{base}/messages/{quote(created['id'], safe='')}/send", authorization, None, "send the draft")
        internet_id = created.get("internetMessageId") if isinstance(created.get("internetMessageId"), str) else None
        named = _INTERNET_ID.fullmatch(internet_id.strip()) if internet_id else None
        record = _sent(named.group(1) if named else message_id, msg, sent_at)
        return record.model_copy(update={"internet_message_id": internet_id})

    def _post(self, url: str, headers: dict[str, str], data: bytes | None, step: str):
        """POST to Graph; a MailerError with the status code or the class of the error unless Graph answers with success."""
        try:
            response = (self.session or requests).post(url, headers=headers, data=data, timeout=GRAPH_TIMEOUT)
        except requests.RequestException as error:
            raise MailerError(f"{type(error).__name__}: could not {step}") from None
        if response.status_code == 401:
            raise SignInLapsed(f"HTTP 401: could not {step}: {SIGN_IN_AGAIN}")
        if not 200 <= response.status_code < 300:
            raise MailerError(f"HTTP {response.status_code}: could not {step}")
        return response


class AutoMailer:
    """send_via "auto" with a folder of the work mailbox read: each message through `microsoft` while an account is signed in, and
    through `other` (the SMTP or the file mailer) otherwise (see the module docstring)."""
    name = "auto"

    def __init__(self, microsoft: MicrosoftMailer, other: Mailer):
        self.microsoft, self.other = microsoft, other

    def current(self) -> Mailer:
        """The mailer a message would go through now. A token cache that cannot be read counts as no account signed in."""
        try:
            signed_in = self.microsoft.account.account() is not None
        except Exception:
            signed_in = False
        return self.microsoft if signed_in else self.other

    def send(self, msg: Message) -> SentRecord:
        return self.current().send(msg)
