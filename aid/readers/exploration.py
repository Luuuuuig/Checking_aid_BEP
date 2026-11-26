"""The exploration readers: Azure OpenAI and Anthropic's models in Microsoft Foundry in Copilot's place, for the made-up documents
of the package only, to learn how well and at what cost each service reads them.

AID_STAND_IN=azure_openai or AID_STAND_IN=foundry switches one on, as AID_STAND_IN=anthropic switches on the stand-in for Copilot
(aid/readers/stand_in.py, whose stand_in_for builds them through this module), and each works only while the settings have the test
mode on. Each takes its key, its address and its deployment from environment variables only, never from a file; in the aid, Foundry
takes the effort of the settings. Azure OpenAI takes the endpoint of its resource, the key and the deployment (AZURE_OPENAI_ENDPOINT,
AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT); Foundry the key, the deployment, and the name of its resource or its full address
(ANTHROPIC_FOUNDRY_API_KEY, AID_FOUNDRY_DEPLOYMENT, and ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL, never both, since
the SDK reads both from the environment as well). A value is taken without the spaces and line ends around it, a variable that is
empty or holds spaces only counts as unset, and a variable that is missing is named, never the value of another. Only for "never
both" does a Foundry address that is there at all count, even empty, as the SDK takes it.
The addresses are https addresses with a host, so that the key never leaves over plain http: AZURE_OPENAI_ENDPOINT is the endpoint
of the resource, with no query and no path but /openai/v1, the forms azure_client.responses_url takes, and ANTHROPIC_FOUNDRY_BASE_URL
any https address. Another form is refused, by the variable and the form it needs, and never repaired. Neither client follows a
redirect (azure_client.py, foundry_client.py), so the key and the document go to that address only, and a redirect, also one to
plain http, is a failure of the service.

Both are refused while the settings name any part of the work mailbox (MAILBOX_FIELDS). The Microsoft sign-in is kept once per
Windows user, not once per folder, so a second folder whose settings name a tenant and a mail folder would be signed in at once and
would save the mail that arrives as files in its own watched folder, outside restricted storage, even though the guard kept those
documents from the services. The exploration therefore runs in a folder of made-up documents of its own, made with
python -m aid.demo --prepare-only --out <folder>.

The client is wrapped in the stand-in's guard (guarded). A request goes out only when each of its documents is, byte for byte, a file
of the made-up folders of the package; the manifest of the unseen cases (made_up_extra.sha256) is left out, so that no document of
evaluation/unseen_cases can reach either service. A refusal begins as the runner's COULD_NOT_READ does and names the service. A
failure of the service is a CopilotError that names the service and the class of the error (for Azure OpenAI a class named by
the cause, aid/readers/azure_client.py), so that the case is read again at a later pass.
"""
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
MAILBOX_FIELDS = ("ms_tenant_id", "ms_client_id", "mailbox", "mail_folder", "order_folder")    # the parts of the work mailbox
NO_SETTING = "the exploration reader for {service} (AID_STAND_IN={value}) needs the environment variable {variable}, never in a file"
NO_TEST_MODE = ("the exploration reader for {service} (AID_STAND_IN={value}) works only in the test mode: switch the test mode on, "
                "or start without it")
NO_MAILBOX = ("the exploration reader for {service} (AID_STAND_IN={value}) works without a mailbox: these settings name {field}; "
              "use a folder of made-up documents of its own")
NOT_MADE_UP = ("Copilot could not read this document: the exploration reader for {service} reads the made-up documents of the "
               "package only, and this document is none of them, so it was not sent")      # begins as COULD_NOT_READ of aid/runner.py
SERVICE_FAILED = "the exploration reader for {service} failed at its service: {kind}"    # the class of the error, never its text
# The {variable} of NO_SETTING for an address in another form: the variable and the form it needs, never the value.
ENDPOINT_FORM = "AZURE_OPENAI_ENDPOINT as an https address such as https://<resource>.openai.azure.com"
BASE_URL_FORM = "ANTHROPIC_FOUNDRY_BASE_URL as an https address such as https://<resource>.services.ai.azure.com/anthropic"


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


def check_no_mailbox(value: str, settings: Settings) -> None:
    """Raise ValueError (NO_MAILBOX) that names the first part of the work mailbox (MAILBOX_FIELDS) the settings set: an exploration
    reader works in a folder of made-up documents of its own (see the module docstring)."""
    if field := next((name for name in MAILBOX_FIELDS if getattr(settings, name)), None):
        raise ValueError(NO_MAILBOX.format(service=SERVICES[value], value=value, field=field))


def guarded(value: str, client, test_text: str | None = None) -> StandIn:
    """The client behind the stand-in's guard, with the texts of its service: it sends a document only when it is byte for byte a
    file of the made-up folders of the package, never one of the manifest of the unseen cases, and no question but the one test
    message `test_text` of the settings tab (StandIn)."""
    service = SERVICES[value]
    return StandIn(client, test_text, allowed=made_up_digests(manifest=None), not_made_up=NOT_MADE_UP.format(service=service),
                   service_failed=SERVICE_FAILED.format(service=service, kind="{kind}"))       # StandIn fills in the kind
