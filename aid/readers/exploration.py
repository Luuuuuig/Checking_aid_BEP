"""The exploration readers: Azure OpenAI and Anthropic's models in Microsoft Foundry in Copilot's place, for the made-up documents
of the package only, to learn how well and at what cost each service reads them.

AID_STAND_IN=azure_openai or AID_STAND_IN=foundry switches one on, as AID_STAND_IN=anthropic switches on the stand-in for Copilot
(aid/readers/stand_in.py, whose stand_in_for builds them through this module), and each works only while the settings have the test
mode on. On that path each takes its key, its address and its deployment from environment variables only, never from a file; in
the aid, Foundry takes the effort of the settings. Azure OpenAI takes the endpoint of its resource, the key and the deployment
(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT); Foundry the key, the deployment, and the name of its
resource or its full address (ANTHROPIC_FOUNDRY_API_KEY, AID_FOUNDRY_DEPLOYMENT, and ANTHROPIC_FOUNDRY_RESOURCE or
ANTHROPIC_FOUNDRY_BASE_URL, never both, since the SDK reads both from the environment as well). A value is taken without the
spaces and line ends around it, a variable that is empty or holds spaces only counts as unset, and a variable that is missing is
named, never the value of another. Only for "never both" does a Foundry address that is there at all count, even empty, as the
SDK takes it.
The addresses are https addresses with a host, so that the key never leaves over plain http: AZURE_OPENAI_ENDPOINT is the endpoint
of the resource, with no query and no path but /openai/v1, the forms azure_client.responses_url takes, and ANTHROPIC_FOUNDRY_BASE_URL
any https address. Another form is refused, by the variable and the form it needs, and never repaired. Neither client follows a
redirect (azure_client.py, foundry_client.py), so the key and the document go to that address only, and a redirect, also one to
plain http, is a failure of the service.

The test mode is the one switch (the owner's decision of 26 November): in it the settings may name a mailbox as well, so that a
mailbox of one's own, to which only made-up mails are sent, is read together with the service. The guard below still sends no
document but the made-up files of the package, byte for byte, also when one arrives as a mail attachment, and nothing of an
e-mail's text goes to either service: the runner asks them to read document files only, and its decider is then MadeUpDecider,
which asks no model to class a document. Company mail never comes to a PC outside the company: the work mailbox is never
connected on such a PC.

The settings tab offers the two services as readers as well (the owner's decision of 26 November): reader "azure_openai" or "foundry" of
the settings (aid/settings.py), which the tab writes only once the service answered its test message, needs no AID_STAND_IN.
settings_client builds the same client from the address and the deployment of the settings and from the key that the tab keeps in the
secrets store of this PC (aid/connections/secrets.py, the store of the Microsoft sign-in, under the name KEYS gives), never from
the settings file or a file of the user's, by the variables above, so that the forms, "not both" and the library are checked as for
the environment. It is
refused, in this order, outside the test mode, for Foundry while the process environment holds the other address variable than the
one the settings give (the SDK reads both), and as exploration_client refuses, each with a SettingsRefused that names the setting,
or the key by its name in the store, and never a value, except the library, whose refusal stays the ValueError NO_FOUNDRY_LIBRARY;
the settings tab names the field in its own words (aid/review/page.py).
The key is read only after the test mode. The runner builds the same reader with reader "azure_openai" or "foundry" (build_deps
of aid/runner.py).

The client is wrapped in the stand-in's guard (guarded). A request goes out only when each of its documents is, byte for byte, a file
of the made-up folders of the package. In the aid, on both paths (stand_in.stand_in_for, and settings_client as the runner calls
it), a document whose sha256 stands in the manifest of the unseen cases (made_up_extra.sha256) goes out as well, as with the
stand-in: by the owner's decision of 26 November 2026 the timed evaluation run reads evaluation/unseen_cases through Azure OpenAI.
The one-document check (explore.py) and the bake-off leave the manifest out, since the check prints what it read and the bake-off
scores it, and nobody may see a case before the run; so does the settings tab's test, which sends no document. A refusal begins as
the runner's COULD_NOT_READ does and names the service. A
failure of the service is a CopilotError that names the service and the class of the error (for Azure OpenAI a class named by
the cause, aid/readers/azure_client.py), so that the case is read again at a later pass.
"""
import os
from collections.abc import Mapping
from decimal import Decimal
from urllib.parse import urlsplit

from aid.readers import llm_client
from aid.readers.azure_client import AzureOpenAIClient
from aid.readers.foundry_client import NO_FOUNDRY_LIBRARY, FoundryClient
from aid.readers.stand_in import StandIn, made_up_digests
from aid.settings import Settings

SERVICES = {"azure_openai": "Azure OpenAI", "foundry": "Anthropic's models in Microsoft Foundry"}    # a value of AID_STAND_IN, its service
# The environment variables of each value, the deployment last; foundry needs one of PLACES as well.
VARIABLES = {"azure_openai": ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"),
             "foundry": ("ANTHROPIC_FOUNDRY_API_KEY", "AID_FOUNDRY_DEPLOYMENT")}
PLACES = ("ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL")     # where the Foundry resource is: its name, or its full address
NO_SETTING = "the exploration reader for {service} (AID_STAND_IN={value}) needs the environment variable {variable}, never in a file"
NO_TEST_MODE = ("the exploration reader for {service} (AID_STAND_IN={value}) works only in the test mode: switch the test mode on, "
                "or start without it")
NOT_MADE_UP = ("Copilot could not read this document: the exploration reader for {service} reads the made-up documents of the "
               "package only, and this document is none of them, so it was not sent")      # begins as COULD_NOT_READ of aid/runner.py
SERVICE_FAILED = "the exploration reader for {service} failed at its service: {kind}"    # the class of the error, never its text
# The {variable} of NO_SETTING for an address in another form: the variable and the form it needs, never the value.
ENDPOINT_FORM = "AZURE_OPENAI_ENDPOINT as an https address such as https://<resource>.openai.azure.com"
BASE_URL_FORM = "ANTHROPIC_FOUNDRY_BASE_URL as an https address such as https://<resource>.services.ai.azure.com/anthropic"

# The readers that the settings tab chooses (settings_client): the name of each service's key in the secrets store of this PC, and
# the setting of each variable, the key by that name.
KEYS = {"azure_openai": "azure_openai_api_key", "foundry": "foundry_api_key"}
SETTING_OF = {"AZURE_OPENAI_ENDPOINT": "azure_openai_endpoint", "AZURE_OPENAI_API_KEY": KEYS["azure_openai"],
              "AZURE_OPENAI_DEPLOYMENT": "azure_openai_deployment", "ANTHROPIC_FOUNDRY_API_KEY": KEYS["foundry"],
              "AID_FOUNDRY_DEPLOYMENT": "foundry_deployment", "ANTHROPIC_FOUNDRY_RESOURCE": "foundry_resource",
              "ANTHROPIC_FOUNDRY_BASE_URL": "foundry_base_url"}
PLACE_SETTINGS = tuple(SETTING_OF[name] for name in PLACES)              # foundry_resource, foundry_base_url
EXAMPLES = {"azure_openai_endpoint": "https://<resource>.openai.azure.com",          # the form an address setting needs
            "foundry_base_url": "https://<resource>.services.ai.azure.com/anthropic"}
# The refusals of such a reader (SettingsRefused): each names the reader and the setting, or the key by its name, never a value.
READER_TEST_MODE = ('the reader "{value}" ({service}) reads the made-up documents of the package in the test mode only: switch the '
                    'test mode on, or choose another reader')
READER_SETTING = 'the reader "{value}" ({service}) needs {setting}: fill it in on the settings tab'
READER_KEY = ('the reader "{value}" ({service}) needs its key in the secrets store of this PC ({setting}): type it on the settings '
              'tab, never in a file')
READER_FORM = 'the reader "{value}" ({service}) needs {setting} as an https address such as {example}'
READER_BOTH = 'the reader "foundry" ({service}) takes foundry_resource or foundry_base_url, not both'
READER_ENVIRONMENT = ('the reader "foundry" ({service}) cannot take {setting} while this process has the environment variable '
                      '{variable}, which the Anthropic library reads as well: remove that variable before the aid starts, or give '
                      '{other} instead')


class SettingsRefused(ValueError):
    """A reader that the settings tab chose that the settings and the secrets store cannot give (settings_client). Its text names
    the reader and the setting, or the key by its name in the store, and never a value. Beside it, what the settings tab names in its
    own words: `kind`, what is wrong ("test_mode", "missing" for a setting, "key", "form" for an address in another form, "both" for
    both Foundry addresses, "environment" for an address beside the other one in the process environment); `settings`, the
    settings it names (test_mode, the setting, or the key by its name in KEYS); and `variable`, the environment variable of
    "environment", else None."""

    def __init__(self, message: str, kind: str, settings: tuple[str, ...], variable: str | None = None):
        super().__init__(message)
        self.kind, self.settings, self.variable = kind, tuple(settings), variable


def exploration_client(value: str, environ: Mapping[str, str], deployment: str | None = None, effort: str = "medium",
                       price: tuple[Decimal, Decimal] | None = None, session=None) -> AzureOpenAIClient | FoundryClient:
    """The client of the service of `value` ("azure_openai" or "foundry"), with its settings from `environ`, each without the spaces
    around it, and not yet guarded (guarded does that). `deployment`, when given and not blank, takes the place of the deployment
    variable (the bake-off gives one for each model). `effort` is Foundry's, `session` the requests session of Azure OpenAI (None:
    a session of its own), and `price` the euros of a million input tokens and of a million output tokens. A variable that is empty
    or holds spaces only counts as unset. Raises ValueError (NO_SETTING) that names the first variable missing and never a value,
    or for foundry the two addresses when both ANTHROPIC_FOUNDRY_RESOURCE and ANTHROPIC_FOUNDRY_BASE_URL are in `environ`, even
    empty or blank ("not both": the SDK reads them from the environment as well, takes one that is there as set, and would refuse
    the pair at every request), or an address in another form than endpoint_address and https_address take (ENDPOINT_FORM,
    BASE_URL_FORM); and for foundry ValueError (NO_FOUNDRY_LIBRARY) when this Python's anthropic library has no client for Foundry."""
    names = VARIABLES[value]
    given = {name: environ.get(name, "").strip() for name in names}
    if deployment is not None and deployment.strip():
        given[names[-1]] = deployment.strip()
    if missing := next((name for name in names if not given[name]), None):
        raise ValueError(NO_SETTING.format(service=SERVICES[value], value=value, variable=missing))
    if value == "azure_openai":
        endpoint, key, chosen = (given[name] for name in names)
        if not endpoint_address(endpoint):
            raise ValueError(NO_SETTING.format(service=SERVICES[value], value=value, variable=ENDPOINT_FORM))
        return AzureOpenAIClient(endpoint=endpoint, api_key=key, deployment=chosen, price_per_mtok=price, session=session)
    key, chosen = (given[name] for name in names)
    either = " or ".join(PLACES)
    resource, base_url = (environ.get(name, "").strip() for name in PLACES)
    if not (resource or base_url):
        raise ValueError(NO_SETTING.format(service=SERVICES[value], value=value, variable=either))
    if all(name in environ for name in PLACES):  # even empty: the SDK reads both too, and would refuse them at every request
        raise ValueError(NO_SETTING.format(service=SERVICES[value], value=value, variable=f"{either}, not both"))
    if base_url and not https_address(base_url):
        raise ValueError(NO_SETTING.format(service=SERVICES[value], value=value, variable=BASE_URL_FORM))
    if llm_client.anthropic is None or not hasattr(llm_client.anthropic, "AnthropicFoundry"):
        raise ValueError(NO_FOUNDRY_LIBRARY)
    return FoundryClient(deployment=chosen, api_key=key, resource=resource or None, base_url=base_url or None, effort=effort,
                         price_per_mtok=price)


def https_address(address: str) -> bool:
    """Whether the address is an https address with a host."""
    try:
        parts = urlsplit(address)
    except ValueError:                           # such as a bracket of an IPv6 host that is not closed
        return False
    return parts.scheme == "https" and bool(parts.hostname)


def endpoint_address(address: str) -> bool:
    """Whether the address is the endpoint of an Azure OpenAI resource in a form that azure_client.responses_url takes: an https
    address with a host, no query and no fragment, and no path but /openai/v1, in any case, with or without a slash after it."""
    return (https_address(address) and "?" not in address and "#" not in address
            and urlsplit(address).path.rstrip("/").lower() in ("", "/openai/v1"))


def guarded(value: str, client, test_text: str | None = None, *, unseen_cases: bool = False) -> StandIn:
    """The client behind the stand-in's guard, with the texts of its service: it sends a document only when it is byte for byte a
    file of the made-up folders of the package, and no question but the one test message `test_text` of the settings tab (StandIn).
    A document of the manifest of the unseen cases goes out only with `unseen_cases` True, which the aid alone gives, on both paths:
    stand_in.stand_in_for, and settings_client as the runner calls it (the evaluation run, by the owner's decision of 26 November
    2026); the one-document check and the bake-off never do, nor the settings tab, whose test sends no document."""
    service = SERVICES[value]
    allowed = made_up_digests() if unseen_cases else made_up_digests(manifest=None)
    return StandIn(client, test_text, allowed=allowed, not_made_up=NOT_MADE_UP.format(service=service),
                   service_failed=SERVICE_FAILED.format(service=service, kind="{kind}"))       # StandIn fills in the kind


def settings_client(value: str, settings: Settings, store, test_text: str | None = None,
                    environ: Mapping[str, str] | None = None, *, unseen_cases: bool = False) -> StandIn:
    """The reader of the service of `value` ("azure_openai" or "foundry") that the settings tab chose (reader of the settings): the
    client of exploration_client behind the guard (guarded), made from the address and the deployment of the settings and from the
    key that `store` (the secrets store of this PC, aid/connections/secrets.py, or anything with its get) holds under KEYS[value],
    each without the spaces around it; `test_text` is the one test message of the settings tab. `environ` is the process
    environment (os.environ when None), which the Anthropic library reads too. Raises SettingsRefused, in this order: outside the
    test mode; for foundry while `environ` holds the other address variable than the one the settings give, even empty
    ("environment"); and as exploration_client refuses, by the setting: a setting missing, the key missing, an address in another
    form, or both Foundry addresses. A mailbox in the settings is no refusal: the test mode is the one switch. The key is read only
    after the test mode. A store that cannot be used raises its own error (StoreRefused, a ValueError), and a Python without the
    Anthropic library's client for Foundry the ValueError NO_FOUNDRY_LIBRARY. `unseen_cases` opens the guard to the documents of
    the manifest of the unseen cases, as stand_in.stand_in_for does: the runner gives it (build_deps of aid/runner.py), for the
    evaluation run in the aid; the settings tab, whose test sends no document, leaves it out."""
    service = SERVICES[value]
    environ = os.environ if environ is None else environ
    if not settings.test_mode:
        raise SettingsRefused(READER_TEST_MODE.format(value=value, service=service), "test_mode", ("test_mode",))
    given = {name: _text(store.get(KEYS[value]) if SETTING_OF[name] == KEYS[value] else getattr(settings, SETTING_OF[name]))
             for name in VARIABLES[value]}
    places = [name for name in PLACES if value == "foundry" and _text(getattr(settings, SETTING_OF[name]))]
    given |= {name: _text(getattr(settings, SETTING_OF[name])) for name in places}
    if len(places) == 1 and (other := next(name for name in PLACES if name not in places)) in environ:
        setting = SETTING_OF[places[0]]
        raise SettingsRefused(READER_ENVIRONMENT.format(service=service, setting=setting, variable=other, other=SETTING_OF[other]),
                              "environment", (setting,), other)
    try:
        client = exploration_client(value, given, effort=settings.effort)
    except ValueError as error:
        raise _refused(value, error) from None
    return guarded(value, client, test_text, unseen_cases=unseen_cases)


def store_key(value: str, store, key: str):
    """Keep `key` in `store` under KEYS[value], as the settings tab does at a save, and give what undoes it: the key that was there
    before put back, or the key removed when there was none, for a save whose write of the fields fails after the key was kept. The
    key that was there stays inside this function; the tab never holds it."""
    name = KEYS[value]
    before = store.get(name)
    store.set(name, key)

    def undo() -> None:
        store.delete(name) if before is None else store.set(name, before)
    return undo


def key_stored(value: str, store) -> bool:
    """Whether `store` holds a key of the service of `value` under KEYS[value], one that is not blank: what the settings tab shows,
    which never holds a key itself (only settings_client takes one). Raises as the store does (StoreRefused, a ValueError)."""
    return bool(_text(store.get(KEYS[value])))


def _text(value) -> str:
    """A setting or a key without the spaces around it; "" for None."""
    return value.strip() if isinstance(value, str) else ""


def _refused(value: str, error: ValueError) -> ValueError:
    """The refusal of exploration_client of a reader that the settings tab chose, said by the setting of the variable it names (the
    key by its name in KEYS) as a SettingsRefused; any other refusal (the library) as it is."""
    service, either = SERVICES[value], " or ".join(PLACES)
    named = {name: ("key" if SETTING_OF[name] == KEYS[value] else "missing", (SETTING_OF[name],)) for name in VARIABLES[value]}
    if value == "azure_openai":
        named[ENDPOINT_FORM] = ("form", ("azure_openai_endpoint",))
    else:
        named |= {either: ("missing", PLACE_SETTINGS), f"{either}, not both": ("both", PLACE_SETTINGS),
                  BASE_URL_FORM: ("form", ("foundry_base_url",))}
    for variable, (kind, settings) in named.items():
        if str(error) == NO_SETTING.format(service=service, value=value, variable=variable):
            setting = " or ".join(settings)
            text = {"missing": READER_SETTING, "key": READER_KEY, "both": READER_BOTH,
                    "form": READER_FORM}[kind].format(value=value, service=service, setting=setting, example=EXAMPLES.get(setting))
            return SettingsRefused(text, kind, settings)
    return error
