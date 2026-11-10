"""What the settings tab of the review page shows and writes for the buyer, without gradio, so that it is tested without a page.

The eleven settings of the form (BUYER_FIELDS) are the buyer's rules: the two price thresholds, the days a delivery date may pass the
supplier's lead time, the first cases of a supplier that always come to the buyer, the wait for a supplier's answer, the name under
the messages, the digest and its time, the buyer's address, the language and the approver's address. shown gives them as the form
shows them: the two price values with a decimal comma in Dutch and a point in English, without trailing zeros beyond two decimals
("2", "2,5"; the euro amount with two decimals at least, "25,00"), the whole numbers as text and the rest as it is. parse reads the
form's texts back, all of them or the fields named only: a number with a comma or a point as its decimal mark and at most two
decimals, so that "1.000", a thousand as a Dutch buyer writes it, is refused and not read as 1; each number in its range, the time
as HH:MM, the addresses by the settings' address rule, the name one line of at most 80 characters (it may be empty). It gives the
values to write and a problem per refused field, a short sentence in the page's language from i18n.STRINGS
("settings_problem:<kind>"), never the value itself. changed_fields gives the fields whose text differs from the text the form
was filled with: a save writes those and no other, so that a setting the buyer did not touch is never written.

The suppliers are the four settings that name a supplier (SUPPLIER_FIELDS): the address for the aid's questions, the lead time in
days, the suppliers whose every case goes to the buyer and those the aid asks nothing by itself. supplier_rows lists every supplier
any of them names and supplier_shown gives one supplier as the supplier form shows it (SUPPLIER_PARTS: its address, its lead time
and its two ticks). supplier_update gives the four settings with the parts given of one supplier changed and its other parts left
as the settings hold them; supplier_changes gives the parts of the form that differ from what it was filled with, so that a save
changes those only. with_supplier sets all four parts of a supplier (or adds it), and without_supplier takes it out of all four.
A name the settings already hold is used exactly as they hold it, also with a space around it, since it is the supplier's name as
the purchase order gives it; any other name is a typed one and is trimmed, and must have 1 to 80 characters on one line. A name
chosen in the page's dropdown is used as the file had it, also when the file no longer holds it at the save (another browser tab
took the supplier out meanwhile). Names are compared exactly as written. Nothing here writes a file: the page writes the values
with update_settings_file (aid/settings.py), which writes the keys whose value changed and keeps every other character of the file.
"""
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation

from aid.review.i18n import STRINGS
from aid.settings import Settings, is_address

BUYER_FIELDS = ("price_pct", "price_abs", "lead_time_tolerance_days", "first_cases_review", "ask_wait_working_days", "buyer_name",
                "digest", "digest_time", "buyer_email", "language", "approver_email")
SUPPLIER_FIELDS = ("supplier_emails", "supplier_lead_times", "review_suppliers", "messages_off_suppliers")
SUPPLIER_PARTS = ("email", "lead_time", "review", "off")       # what the supplier form holds of one supplier, beside its name
NAME_LENGTH = 80                                 # the longest buyer's name and supplier's name
DECIMALS = 2                                     # the most decimals of a percentage and of an amount
_NUMBER = re.compile(r"\d+(?:[.,](\d+))?")       # a number as the form takes one: digits, and a comma or a point with digits after it
_WHOLE = re.compile(r"\d+")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
# The whole numbers of the form: their lowest and highest value and the kind of their problem.
_WHOLE_RANGES = {"lead_time_tolerance_days": (0, 365, "days"), "first_cases_review": (0, 1000, "cases"), "ask_wait_working_days": (1, 60, "wait")}
_CHOICES = {"digest": (("daily", "per_case"), "digest"), "language": (("nl", "en"), "language")}


def shown(settings: Settings, language: str) -> dict:
    """The eleven settings as the form shows them in the page's language (see the module docstring), in the order of BUYER_FIELDS."""
    values = {field: getattr(settings, field) for field in BUYER_FIELDS}
    values["price_pct"] = _decimal_text(settings.price_pct, 0, language)
    values["price_abs"] = _decimal_text(settings.price_abs, 2, language)
    for field in _WHOLE_RANGES:
        values[field] = str(values[field])
    return values


def parse(form: dict, language: str, only: Iterable[str] | None = None) -> tuple[dict, dict[str, str]]:
    """The values to write of the form's texts (keyed by the fields of BUYER_FIELDS, in their order), and the problem of each
    refused field, keyed by its name, in the page's language. With `only`, the fields it names are read and no other: a field it
    leaves out is neither a value nor a problem. A field the form lacks counts as empty. The two price values are written as text
    with a point ("2.5"), as the settings file holds its amounts, and take at most two decimals (the problem of "1.000" says so,
    and to write a thousand as 1000); the whole numbers as numbers; the time with two digits each ("07:05")."""
    strings, values, problems = STRINGS[language], {}, {}
    wanted = set(BUYER_FIELDS if only is None else only)

    def refuse(field: str, kind: str) -> None:
        problems[field] = strings[f"settings_problem:{kind}"]

    for field in BUYER_FIELDS:
        if field not in wanted:
            continue
        text = _text(form.get(field))
        if field in ("price_pct", "price_abs"):
            number = _number(text)
            if number is not None and len(_NUMBER.fullmatch(text)[1] or "") > DECIMALS:
                refuse(field, "decimals")
            elif number is None or (field == "price_pct" and number > 100):
                refuse(field, "percent" if field == "price_pct" else "amount")
            else:
                values[field] = str(number)
        elif field in _WHOLE_RANGES:
            low, high, kind = _WHOLE_RANGES[field]
            whole = _whole(text, low, high)
            if whole is None:
                refuse(field, kind)
            else:
                values[field] = whole
        elif field in _CHOICES:
            allowed, kind = _CHOICES[field]
            if text in allowed:
                values[field] = text
            else:
                refuse(field, kind)
        elif field == "digest_time":
            match = _TIME.fullmatch(text)
            if match and int(match[1]) <= 23 and int(match[2]) <= 59:
                values[field] = f"{int(match[1]):02d}:{int(match[2]):02d}"
            else:
                refuse(field, "time")
        elif field in ("buyer_email", "approver_email"):
            if is_address(text):
                values[field] = text
            else:
                refuse(field, "address")
        else:                                    # buyer_name
            if len(text) <= NAME_LENGTH and not ("\n" in text or "\r" in text):
                values[field] = text
            else:
                refuse(field, "name")
    return values, problems


def changed_fields(form: Mapping, filled: Mapping) -> list[str]:
    """The fields of BUYER_FIELDS, in their order, whose text in the form differs from the text the form was filled with. A space
    around a text is no difference, and a field either of them lacks counts as empty."""
    return [field for field in BUYER_FIELDS if _text(form.get(field)) != _text(filled.get(field))]


def supplier_names(settings: Settings) -> list[str]:
    """Every supplier named in any of the four supplier settings, sorted by name without regard to case."""
    names = {*settings.supplier_emails, *settings.supplier_lead_times, *settings.review_suppliers, *settings.messages_off_suppliers}
    return sorted(names, key=lambda name: (name.casefold(), name))


def supplier_rows(settings: Settings) -> list[list]:
    """Every supplier named in any of the four supplier settings, sorted by name without regard to case: [name, e-mail address or
    "", lead time in days or "", always to the buyer, no questions by itself]."""
    return [[name, settings.supplier_emails.get(name, ""), settings.supplier_lead_times.get(name, ""), name in settings.review_suppliers,
             name in settings.messages_off_suppliers] for name in supplier_names(settings)]


def supplier_shown(settings: Settings, name: str) -> dict:
    """One supplier as the supplier form shows it (SUPPLIER_PARTS): its address and its lead time as text ("" when the settings
    hold none; a lead time of 0 days is "0") and its two ticks. A name the settings do not hold gives the empty form."""
    lead_time = settings.supplier_lead_times.get(name)
    return {"email": settings.supplier_emails.get(name, ""), "lead_time": "" if lead_time is None else str(lead_time),
            "review": name in settings.review_suppliers, "off": name in settings.messages_off_suppliers}


def supplier_changes(form: Mapping, filled: Mapping) -> dict:
    """The parts of the supplier form (SUPPLIER_PARTS) whose value differs from the one the form was filled with, with the value
    the form holds. A space around a text is no difference, and neither is a part either of them lacks beside an empty one."""
    def held(values: Mapping, part: str):
        return bool(values.get(part)) if part in ("review", "off") else _text(values.get(part))
    return {part: form.get(part) for part in SUPPLIER_PARTS if held(form, part) != held(filled, part)}


def supplier_update(settings: Settings, name, parts: Mapping, language: str = "nl", chosen: bool = False) -> tuple[dict, dict[str, str]]:
    """The four supplier settings with the parts given of one supplier changed (the keys of SUPPLIER_PARTS that `parts` holds), and
    the problem of each refused value, keyed by "supplier_name", "supplier_email" or "supplier_lead_time", in the language. A part
    that is not given stays as the settings hold it and is not read. An empty address takes the supplier out of supplier_emails,
    else it must be an address; an empty lead time takes it out of supplier_lead_times, else a whole number from 0 to 365; the two
    ticks put it in or take it out of the two lists. A supplier already named keeps its place in each setting; a new one is added
    at the end. The name is used as the settings hold it, or trimmed and checked when they do not hold it (see the module
    docstring); a name chosen in the page's dropdown (chosen) is the name as the file had it and is used as it is, also when the
    settings no longer hold it."""
    strings, problems = STRINGS[language], {}
    name, listed = (name, True) if chosen else _name(settings, name)
    if not listed and (not name or len(name) > NAME_LENGTH or "\n" in name or "\r" in name):
        problems["supplier_name"] = strings["settings_problem:supplier_name"]
    email, lead_text = _text(parts.get("email")), _text(parts.get("lead_time"))
    if "email" in parts and email and not is_address(email):
        problems["supplier_email"] = strings["settings_problem:address"]
    days = _whole(lead_text, 0, 365) if lead_text else None
    if "lead_time" in parts and lead_text and days is None:
        problems["supplier_lead_time"] = strings["settings_problem:days"]
    if problems:
        return {}, problems
    values = _copies(settings)
    if "email" in parts:
        _put(values["supplier_emails"], name, email or None)
    if "lead_time" in parts:
        _put(values["supplier_lead_times"], name, days)
    if "review" in parts:
        _tick(values["review_suppliers"], name, bool(parts["review"]))
    if "off" in parts:
        _tick(values["messages_off_suppliers"], name, bool(parts["off"]))
    return values, {}


def with_supplier(settings: Settings, name, email, lead_time, always_review, no_messages, language: str = "nl") -> tuple[dict, dict[str, str]]:
    """The four supplier settings with all four values of the supplier set (supplier_update with every part), and the problem of
    each refused value. A supplier left without an address, a lead time and both ticks is in none of the four."""
    return supplier_update(settings, name, {"email": email, "lead_time": lead_time, "review": always_review, "off": no_messages}, language)


def without_supplier(settings: Settings, name: str) -> dict:
    """The four supplier settings with the supplier taken out of all four: the name as the settings hold it, or else trimmed."""
    name, values = _name(settings, name)[0], _copies(settings)
    _put(values["supplier_emails"], name, None)
    _put(values["supplier_lead_times"], name, None)
    _tick(values["review_suppliers"], name, False)
    _tick(values["messages_off_suppliers"], name, False)
    return values


def _name(settings: Settings, name) -> tuple[str, bool]:
    """A supplier's name as it is used, and whether the settings hold it: a name they hold exactly as given (the dropdown gives it
    so) is used as it is, any other is a typed name and is trimmed."""
    name = name if isinstance(name, str) else ""
    if name in supplier_names(settings):
        return name, True
    return name.strip(), name.strip() in supplier_names(settings)


def _text(value) -> str:
    """A value of a form as text without the space around it; "" for None."""
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()


def _copies(settings: Settings) -> dict:
    """The four supplier settings as copies, in the order of SUPPLIER_FIELDS, so that the settings themselves are not changed."""
    return {field: (dict if isinstance(getattr(settings, field), dict) else list)(getattr(settings, field)) for field in SUPPLIER_FIELDS}


def _put(mapping: dict, name: str, value) -> None:
    """Set the name's value in its place (a new name at the end), or take the name out when the value is None."""
    if value is None:
        mapping.pop(name, None)
    else:
        mapping[name] = value


def _tick(names: list, name: str, ticked: bool) -> None:
    if ticked and name not in names:
        names.append(name)
    elif not ticked:
        names[:] = [other for other in names if other != name]


def _number(text: str) -> Decimal | None:
    """A number of 0 or more written with a comma or a point as its decimal mark, or None."""
    if not _NUMBER.fullmatch(text):
        return None
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


def _whole(text: str, low: int, high: int) -> int | None:
    """A whole number from low to high written in digits, or None."""
    return int(text) if _WHOLE.fullmatch(text) and low <= int(text) <= high else None


def _decimal_text(value: Decimal, places: int, language: str) -> str:
    """The value without trailing zeros beyond `places` decimals, with a decimal comma in Dutch and a point in English."""
    text = format(value, "f")
    if "." in text:
        whole, _, fraction = text.partition(".")
        fraction = fraction.rstrip("0").ljust(places, "0")
        text = f"{whole}.{fraction}" if fraction else whole
    elif places:
        text = f"{text}.{'0' * places}"
    return text.replace(".", ",") if language == "nl" else text
