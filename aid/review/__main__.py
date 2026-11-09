"""python3 -m aid.review --settings settings.json [--share]: the review page on this machine.

The dependencies come from the settings: the PO store at po_store_root, read with the settings' authorization limit; the parser as the
reader; no decider; the prepared writer into <cases_root>/prepared; the file mailer into <cases_root>/outbox, from the aid's address;
and the clock of the machine. The page is served on 127.0.0.1 only. --share opens a public gradio link and is for the made-up set
only: it is refused, with exit status 2 and the reason of refuse_share, when the settings hold company values, because the
company's data are for internal use only. A settings file that is missing or not valid ends the command with exit status 2 and a
message that names the file and the fields, never a value. The Colab notebook asks refuse_share as well before it opens its link;
refuse_share imports without gradio, which build_app imports when it builds the page. The page's settings tab writes into the
settings file of the command; on a page opened with --share it offers nothing.
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from aid.decider import NoDecider
from aid.execution.writer import PreparedWriter
from aid.messaging import FileMailer
from aid.pipeline import Deps, read_with_parser
from aid.po_store import PoStore
from aid.review.app import build_app
from aid.settings import Settings, load_settings

MADE_UP_DOMAINS = ("example.com", "example.org")   # the domains of the made-up addresses


def company_values(settings: Settings) -> list[str]:
    """What in the settings marks them as the company's, by the names of the settings and never their values; [] for settings of
    the made-up set. A company value is: exact_base_url, authorization_limit, smtp_host or smtp_user set; ms_tenant_id or
    mail_folder set, since the cases may then come from the work mailbox; exact_endpoints or exact_fields not empty; an address of
    supplier_emails, or buyer_email, aid_email or approver_email, outside example.com and example.org (a domain under them counts
    as theirs). The folders are no mark: the made-up set may be kept anywhere (in Colab, in Drive)."""
    found = [f"{name} is set" for name in ("exact_base_url", "authorization_limit", "smtp_host", "smtp_user", "ms_tenant_id", "mail_folder")
             if getattr(settings, name) is not None]
    found += [f"{name} is not empty" for name in ("exact_endpoints", "exact_fields") if getattr(settings, name)]
    if not all(_made_up(address) for address in settings.supplier_emails.values()):
        found.append("supplier_emails holds an address outside example.com and example.org")
    found += [f"{name} is outside example.com and example.org" for name in ("buyer_email", "aid_email", "approver_email")
              if not _made_up(getattr(settings, name))]
    return found


def refuse_share(settings: Settings) -> str | None:
    """Why a public share link is refused for these settings (they hold company values, see company_values); None for the made-up
    set."""
    found = company_values(settings)
    if not found:
        return None
    return f"--share is refused: the data are for internal use only, and these settings hold company values ({'; '.join(found)})"


def _unreadable(path: Path, error: Exception) -> str:
    """Why a settings file cannot be read, naming the file and the fields, never a value: a ValidationError by the fields it names,
    a missing or unreadable file by the class of the error, anything else (a load_settings that names the fields only, a JSON
    error) by its message."""
    if isinstance(error, ValidationError):
        fields = sorted({".".join(str(part) for part in problem["loc"]) or "(the file)" for problem in error.errors()})
        return f"{path}: not valid ({', '.join(fields)})"
    if isinstance(error, OSError):
        return f"{path}: {type(error).__name__}"
    return str(error)


def _made_up(address: str) -> bool:
    """Whether an address is on example.com or example.org, or on a domain under them (sales@s07.example.com)."""
    domain = address.rpartition("@")[2].strip().lower()
    return any(domain == made_up or domain.endswith(f".{made_up}") for made_up in MADE_UP_DOMAINS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m aid.review", description="The review page of the checking aid.")
    parser.add_argument("--settings", type=Path, required=True, help="the settings file")
    parser.add_argument("--share", action="store_true", help="open a public gradio link (the made-up set only)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
    except (OSError, ValueError) as error:
        print(f"the settings cannot be read: {_unreadable(args.settings, error)}", file=sys.stderr)
        return 2
    if args.share and (reason := refuse_share(settings)) is not None:
        print(reason, file=sys.stderr)
        return 2
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"         # before build_app imports gradio
    cases = settings.cases_root
    deps = Deps(settings=settings, store=PoStore(settings.po_store_root, settings.authorization_limit), reader=read_with_parser,
                decider=NoDecider(), writer=PreparedWriter(cases / "prepared"), mailer=FileMailer(cases / "outbox", sender=settings.aid_email),
                clock=datetime.now)
    build_app(deps, settings_file=args.settings, share=args.share).launch(server_name="127.0.0.1", share=args.share)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
