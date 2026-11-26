"""The exploration readers (aid/readers/exploration.py): Azure OpenAI and Anthropic's models in Microsoft Foundry in Copilot's place
while the process has AID_STAND_IN=azure_openai or foundry, for the made-up documents of the package only (never a document of the
unseen cases), without a mailbox and in the test mode only. No test calls a service."""
import hashlib
import json
import os
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests

from aid.demo import RECORDED_DIR
from aid.readers import exploration, llm_client, stand_in
from aid.readers.azure_client import NO_COUNT, AzureOpenAIClient, AzureServiceError
from aid.readers.copilot_client import CopilotError
from aid.readers.foundry_client import NO_FOUNDRY_LIBRARY
from aid.readers.llm_client import LlmResponse, ReaderError, RecordedClient
from aid.runner import COULD_NOT_READ, build_deps
from aid.tests.test_runner import settings_file
from aid.tests.test_stand_in import MADE_UP_PDF, MADE_UP_PNG, ON, UNSEEN_DOCUMENTS, Recording, request

EXPLORATION = ("azure_openai", "foundry")
# The variables of each exploration reader, with made-up values.
SET = {"azure_openai": {"AZURE_OPENAI_ENDPOINT": "https://made-up.openai.azure.com", "AZURE_OPENAI_API_KEY": "made-up-key-for-the-test",
                        "AZURE_OPENAI_DEPLOYMENT": "dep"},
       "foundry": {"ANTHROPIC_FOUNDRY_API_KEY": "made-up-key-for-the-test", "ANTHROPIC_FOUNDRY_RESOURCE": "made-up-resource",
                   "AID_FOUNDRY_DEPLOYMENT": "dep"}}
# A value that each part of the work mailbox takes in a settings file (aid/settings.py), made up.
MAILBOX = {"ms_tenant_id": "contoso.onmicrosoft.com", "ms_client_id": "11111111-2222-3333-4444-555555555555",
           "mailbox": "buyer@example.com", "mail_folder": "made-up-mail-folder-id", "order_folder": "made-up-order-folder-id"}
TEST_TEXT = "Answer with the word OK only."


def switched_on(value: str, **more: str) -> dict[str, str]:
    """An environment that switches the exploration reader of `value` on, with each of its variables set."""
    return {"AID_STAND_IN": value} | SET[value] | more


class Service:
    """A client in the place of a service's: it records each request it is sent or asked to count, and answers with the test's
    answer, or raises the test's failure."""

    def __init__(self, name: str, failure: Exception | None = None):
        self.name, self.failure, self.requests, self.counted = name, failure, [], []

    def complete(self, req):
        self.requests.append(req)
        if self.failure is not None:
            raise self.failure
        return LlmResponse(text=json.dumps({"answer": "OK"}), model=self.name, input_tokens=1, output_tokens=1, seconds=0.0)

    def count_tokens(self, req):
        self.counted.append(req)
        return 42


@pytest.fixture
def made(monkeypatch):
    """Each client that exploration_client makes, in the order made: the real classes, which call no service when they are made."""
    made = []

    def recorded(cls):
        def make(*args, **kwargs):
            made.append(cls(*args, **kwargs))
            return made[-1]
        return make
    monkeypatch.setattr(exploration, "AzureOpenAIClient", recorded(exploration.AzureOpenAIClient))
    monkeypatch.setattr(exploration, "FoundryClient", recorded(exploration.FoundryClient))
    return made


def test_the_switch_is_on_for_the_three_values(tmp_path):
    assert stand_in.VALUES == ("anthropic", "azure_openai", "foundry")
    for on in ("anthropic", " Azure_OpenAI ", "FOUNDRY"):
        assert stand_in.active({"AID_STAND_IN": on})
    for off in ("azure", ""):
        assert not stand_in.active({"AID_STAND_IN": off})
    assert stand_in.value({"AID_STAND_IN": " Azure_OpenAI "}) == "azure_openai" and stand_in.value({}) == ""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    for written, value in ((" Azure_OpenAI ", "azure_openai"), ("FOUNDRY", "foundry")):
        guard = stand_in.stand_in_for(settings, switched_on(value, AID_STAND_IN=written))
        assert guard.name == f"{value}:dep" and guard.allowed == stand_in.made_up_digests(manifest=None)
        assert stand_in.extra_digests() and not guard.allowed & stand_in.extra_digests()      # never a document of the unseen cases


def test_anthropic_is_unchanged(tmp_path, monkeypatch):
    """AID_STAND_IN=anthropic keeps its allowed set, with the manifest of the unseen cases, and its texts, and it is not refused while
    the settings name every part of the work mailbox."""
    monkeypatch.setattr(stand_in, "AnthropicClient", Recording)
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True, **MAILBOX)
    guard = stand_in.stand_in_for(settings, ON)
    assert guard.allowed == stand_in.made_up_digests() and stand_in.extra_digests() and stand_in.extra_digests() <= guard.allowed
    assert (guard.not_made_up, guard.service_failed) == (stand_in.NOT_MADE_UP, stand_in.SERVICE_FAILED) == (
        "Copilot could not read this document: the stand-in for Copilot reads the made-up documents only, and this document is none "
        "of them, so it was not sent", "the stand-in for Copilot could not reach its service: {kind}")
    with pytest.raises(ReaderError) as refused:
        guard.complete(request(pdf=MADE_UP_PDF.read_bytes() + b"\n"))
    assert str(refused.value) == stand_in.NOT_MADE_UP


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_exploration_readers_need_the_test_mode(tmp_path, made, value):
    """Outside the test mode the exploration readers are refused first, before the mailbox and the variables are looked at."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=False, ms_tenant_id=MAILBOX["ms_tenant_id"])
    with pytest.raises(ValueError) as refused:
        build_deps(settings, {"AID_STAND_IN": value})
    assert str(refused.value) == exploration.NO_TEST_MODE.format(service=exploration.SERVICES[value], value=value)
    assert made == []


MAILBOX_PARTS = ("ms_tenant_id", "ms_client_id", "mailbox", "mail_folder", "order_folder")      # written out, not taken from the code


def test_the_mailbox_fields_are_the_five_parts_of_the_work_mailbox():
    assert exploration.MAILBOX_FIELDS == MAILBOX_PARTS


@pytest.mark.parametrize("value", EXPLORATION)
@pytest.mark.parametrize("field", MAILBOX_PARTS)
def test_the_exploration_readers_refuse_any_part_of_the_work_mailbox(tmp_path, made, value, field):
    """Each part of the work mailbox is refused on its own, by its field, before the variables are looked at. A settings file holds
    order_folder only beside mail_folder, so each field is set alone on a copy of the settings."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    with pytest.raises(ValueError) as refused:
        stand_in.stand_in_for(settings.model_copy(update={field: MAILBOX[field]}), {"AID_STAND_IN": value})
    assert str(refused.value) == exploration.NO_MAILBOX.format(service=exploration.SERVICES[value], value=value, field=field)
    assert made == []
    guard = stand_in.stand_in_for(settings, switched_on(value))                           # without it, the reader is made
    assert guard.name == f"{value}:dep" and guard.allowed == stand_in.made_up_digests(manifest=None)
    assert stand_in.extra_digests() and not guard.allowed & stand_in.extra_digests()


@pytest.mark.parametrize("value, missing, named", [
    *((value, (name,), name) for value in EXPLORATION for name in exploration.VARIABLES[value]),
    ("foundry", exploration.PLACES, "ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL"),
])
def test_a_missing_variable_is_named_without_its_value(tmp_path, made, value, missing, named):
    """A variable that is not there, is empty or holds spaces only is missing: it is named, and no value of the others is said."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    others = {name: "secret-123" for name in SET[value] if name not in missing}
    for unset in ({}, dict.fromkeys(missing, ""), dict.fromkeys(missing, " \n")):
        with pytest.raises(ValueError) as refused:
            build_deps(settings, {"AID_STAND_IN": value} | others | unset)
        text = str(refused.value)
        assert all(name in text for name in missing) and "secret-123" not in text
        assert text == exploration.NO_SETTING.format(service=exploration.SERVICES[value], value=value, variable=named)
    assert made == []


@pytest.mark.parametrize("value", EXPLORATION)
def test_a_key_in_a_file_is_never_taken_in_place_of_the_variable(tmp_path, made, monkeypatch, value):
    """The key comes from the environment variable only. With the variable missing from the environment, a key written into the
    folder's settings file (as the value of a field the file allows, under the variable's name) and into a file in the working
    folder (.env, and a file named after the variable) is not taken: building the reader is refused with the sentence that names
    the missing variable, and the key is in no text of it."""
    variable = next(name for name in exploration.VARIABLES[value] if name.endswith("_API_KEY"))
    key = "made-up-key-in-a-file"
    for name, setting in switched_on(value).items():
        monkeypatch.setenv(name, setting)
    monkeypatch.delenv(variable)
    settings, path = settings_file(tmp_path, reader="llm", test_mode=True, exact_fields={variable: key})
    assert key in path.read_text(encoding="utf-8") and settings.exact_fields == {variable: key}
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text(f"{variable}={key}\n", encoding="utf-8")
    (work / variable).write_text(key + "\n", encoding="utf-8")
    monkeypatch.chdir(work)
    with pytest.raises(ValueError) as refused:
        build_deps(settings, os.environ)
    assert str(refused.value) == exploration.NO_SETTING.format(service=exploration.SERVICES[value], value=value, variable=variable)
    assert key not in str(refused.value) and made == []

def test_values_are_stripped():
    """A value pasted with spaces or a line end reaches the client without them. A deployment given (the bake-off gives one for each
    model) takes the place of the deployment variable, and the effort, the price and the session given reach the client."""
    price, session = (Decimal("2"), Decimal("8")), object()
    azure = exploration.exploration_client("azure_openai", {"AZURE_OPENAI_ENDPOINT": " https://made-up.openai.azure.com/ \n",
                                                            "AZURE_OPENAI_API_KEY": " k \n", "AZURE_OPENAI_DEPLOYMENT": " dep \n"},
                                           price=price, session=session)
    assert (azure.url, azure._api_key, azure.deployment, azure.name) == (
        "https://made-up.openai.azure.com/openai/v1/responses", "k", "dep", "azure_openai:dep")
    assert azure.price_per_mtok == price and azure._session is session
    foundry = exploration.exploration_client("foundry", {"ANTHROPIC_FOUNDRY_API_KEY": " k \n", "ANTHROPIC_FOUNDRY_RESOURCE": " res \n",
                                                         "AID_FOUNDRY_DEPLOYMENT": " dep \n"}, effort="high", price=price)
    assert (foundry._api_key, foundry._resource, foundry._base_url, foundry.model, foundry.name) == ("k", "res", None, "dep", "foundry:dep")
    assert foundry.effort == "high" and foundry.price_per_mtok == price
    for value in EXPLORATION:
        without = {name: text for name, text in SET[value].items() if not name.endswith("_DEPLOYMENT")}
        assert exploration.exploration_client(value, without, deployment=" dep2 \n").name == f"{value}:dep2"


def test_foundry_takes_the_resource_or_the_base_url_and_not_both(made):
    """One of the two variables says where the Foundry resource is, the other one absent. Both in the environment are refused before
    a client is made, by their names and never their values, also when one of them is empty or holds spaces only: the SDK reads
    both from the environment as well, takes a variable that is there as set, and would refuse the pair at every request."""
    url = "https://made-up-resource.services.ai.azure.com/anthropic"
    rest = {name: text for name, text in SET["foundry"].items() if name not in exploration.PLACES}
    for places, where in (({"ANTHROPIC_FOUNDRY_RESOURCE": " res \n"}, ("res", None)),
                          ({"ANTHROPIC_FOUNDRY_BASE_URL": f" {url} \n"}, (None, url))):
        client = exploration.exploration_client("foundry", rest | places)
        assert (client._resource, client._base_url) == where
    for places in ({"ANTHROPIC_FOUNDRY_RESOURCE": "made-up-resource", "ANTHROPIC_FOUNDRY_BASE_URL": url},
                   {"ANTHROPIC_FOUNDRY_RESOURCE": "made-up-resource", "ANTHROPIC_FOUNDRY_BASE_URL": ""},
                   {"ANTHROPIC_FOUNDRY_RESOURCE": " \n", "ANTHROPIC_FOUNDRY_BASE_URL": f" {url} \n"}):
        with pytest.raises(ValueError) as refused:
            exploration.exploration_client("foundry", rest | places)
        assert str(refused.value) == exploration.NO_SETTING.format(service=exploration.SERVICES["foundry"], value="foundry",
                                                                   variable="ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL, not both")
        assert "made-up" not in str(refused.value)
    assert len(made) == 2


def test_foundry_takes_the_effort_of_the_settings(tmp_path):
    """In the aid, Foundry reads with the effort of the settings, through stand_in_for as the runner and the settings tab build it."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True, effort="high")
    assert stand_in.stand_in_for(settings, switched_on("foundry")).client.effort == "high"


@pytest.mark.parametrize("library", [None, SimpleNamespace(Anthropic=object)])
def test_foundry_is_refused_without_the_library_s_client_for_foundry(monkeypatch, made, library):
    """Without the anthropic library, or with one that has no client for Foundry, foundry is refused with a ValueError before a
    client is made, as the stand-in is without its library; Azure OpenAI needs no such library."""
    monkeypatch.setattr(llm_client, "anthropic", library)
    with pytest.raises(ValueError) as refused:
        exploration.exploration_client("foundry", switched_on("foundry"))
    assert str(refused.value) == NO_FOUNDRY_LIBRARY and made == []
    assert exploration.exploration_client("azure_openai", switched_on("azure_openai")).name == "azure_openai:dep"


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_exploration_readers_allow_the_made_up_folders_only(value):
    """The allowed set of an exploration reader is the made-up folders of the package, without the manifest of the unseen cases that
    the stand-in takes as well; checked here also where evaluation/unseen_cases is not in the repository."""
    guard = exploration.guarded(value, Service(f"{value}:dep"))
    assert guard.allowed and guard.allowed == stand_in.made_up_digests(manifest=None)
    assert stand_in.extra_digests() and not stand_in.extra_digests() & guard.allowed
    assert guard.allowed | stand_in.extra_digests() == stand_in.made_up_digests()


@pytest.mark.skipif(not UNSEEN_DOCUMENTS.is_dir(), reason="evaluation/unseen_cases is not in this repository")
def test_a_document_of_the_unseen_cases_goes_to_anthropic_but_not_to_the_exploration_readers(tmp_path, monkeypatch):
    first = stand_in.EXTRA_MANIFEST.read_text(encoding="ascii").split()[0]
    paths = [path for path in sorted(UNSEEN_DOCUMENTS.iterdir()) if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == first]
    document = paths[0].read_bytes()
    for value in EXPLORATION:
        fake = Service(f"{value}:dep")
        with pytest.raises(ReaderError) as refused:
            exploration.guarded(value, fake).complete(request(pdf=document))
        assert str(refused.value).startswith("Copilot could not read this document:") and exploration.SERVICES[value] in str(refused.value)
        assert fake.requests == []
    monkeypatch.setattr(stand_in, "AnthropicClient", Recording)
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    anthropic = stand_in.stand_in_for(settings, ON)
    anthropic.complete(request(pdf=document))
    assert [req.pdf for req in anthropic.client.requests] == [document]


@pytest.mark.parametrize("value", EXPLORATION)
def test_a_company_document_is_refused_before_any_call(value):
    """Only a file of the made-up set goes out. The made-up confirmation with one byte more is refused with the words of a document
    Copilot could not read, naming the service, as are a foreign image and a request without a document; nothing is sent."""
    fake = Service(f"{value}:dep")
    guard = exploration.guarded(value, fake)
    pdf, png = MADE_UP_PDF.read_bytes(), MADE_UP_PNG.read_bytes()
    for refused in (request(pdf=pdf + b"\n"), request(images=[png[:-1]]), request(pdf=pdf, images=[b"\x89PNG made-up"]), request()):
        with pytest.raises(ReaderError) as error:
            guard.complete(refused)
        assert str(error.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
    assert fake.requests == [] and exploration.NOT_MADE_UP.startswith(COULD_NOT_READ.split("{")[0])
    guard.complete(request(pdf=pdf))
    guard.complete(request(images=[png]))
    assert len(fake.requests) == 2


@pytest.mark.parametrize("value", ("anthropic", *EXPLORATION))
def test_count_tokens_is_guarded_the_same_way(value):
    """A count sends the request to the service as an answer does: only a request whose every document is a file of the made-up set
    is counted, by the stand-in as by the exploration readers."""
    fake = Service(f"{value}:dep")
    guard = stand_in.StandIn(fake) if value == "anthropic" else exploration.guarded(value, fake)
    expected = stand_in.NOT_MADE_UP if value == "anthropic" else exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
    assert guard.count_tokens(request(pdf=MADE_UP_PDF.read_bytes())) == 42
    for refused in (request(pdf=MADE_UP_PDF.read_bytes() + b"\n"), request()):
        with pytest.raises(ReaderError) as error:
            guard.count_tokens(refused)
        assert str(error.value) == expected
    assert len(fake.counted) == 1 and fake.requests == []


def test_a_failed_count_is_a_copilot_error_naming_the_class_only():
    """A count that fails at the service is a CopilotError, as a failed answer is: its text names the class of the error and never
    the error's own text. A ReaderError of the client, such as the count that Azure OpenAI cannot give, stays a ReaderError."""
    class Failing(Service):
        def count_tokens(self, req):
            self.counted.append(req)
            raise ConnectionError("made-up outage of the service")
    for value in ("anthropic", *EXPLORATION):
        fake = Failing(f"{value}:dep")
        guard = stand_in.StandIn(fake) if value == "anthropic" else exploration.guarded(value, fake)
        failed_text = (stand_in.SERVICE_FAILED if value == "anthropic"
                       else exploration.SERVICE_FAILED.replace("{service}", exploration.SERVICES[value]))
        with pytest.raises(CopilotError) as failed:
            guard.count_tokens(request(pdf=MADE_UP_PDF.read_bytes()))
        assert str(failed.value) == failed_text.format(kind="ConnectionError") and "made-up outage" not in str(failed.value)
        assert len(fake.counted) == 1
    azure = exploration.guarded("azure_openai", exploration.exploration_client("azure_openai", SET["azure_openai"]))
    with pytest.raises(ReaderError, match=NO_COUNT):
        azure.count_tokens(request(pdf=MADE_UP_PDF.read_bytes()))


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_test_message_goes_out_and_no_other_question(value):
    fake = Service(f"{value}:dep")
    guard = exploration.guarded(value, fake, test_text=TEST_TEXT)
    assert guard.ask(TEST_TEXT).text == "OK"
    for text, context in (("Tell me about the order 250105.", None), (TEST_TEXT, [{"made": "up"}])):
        with pytest.raises(ReaderError):
            guard.ask(text, context)
    with pytest.raises(ReaderError):                 # the runner's reader has no test text: it asks nothing without a document
        exploration.guarded(value, fake).ask(TEST_TEXT)
    assert [(req.user_text, req.pdf, req.images) for req in fake.requests] == [(TEST_TEXT, None, [])]


@pytest.mark.parametrize("value, failure", [("azure_openai", AzureServiceError("401 x", status_code=401)),
                                            ("foundry", ConnectionError("made-up outage of the service"))])
def test_a_service_failure_is_a_copilot_error_naming_the_class_only(value, failure):
    """A failure of the service is a failure of Copilot as a whole for the runner, so the case waits for a later pass. Its text names
    the service and the class of the error, never the error's own text."""
    with pytest.raises(CopilotError) as failed:
        exploration.guarded(value, Service(f"{value}:dep", failure)).complete(request(pdf=MADE_UP_PDF.read_bytes()))
    assert str(failed.value) == exploration.SERVICE_FAILED.format(service=exploration.SERVICES[value], kind=type(failure).__name__)
    assert str(failure) not in str(failed.value)
    assert exploration.SERVICE_FAILED == "the exploration reader for {service} failed at its service: {kind}"    # a refused key is reached


class StubFoundry:
    """The SDK's AnthropicFoundry in the place of the service's: its stream answers as the spec of MU05 says, and nothing leaves the PC."""

    def __init__(self, **kwargs):
        self.messages = SimpleNamespace(stream=lambda **request: self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        from aid.tests.helpers import answer_from_spec
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=answer_from_spec("MU05"))], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200), model="claude-x")


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_runner_reads_a_made_up_confirmation_through_the_exploration_reader(tmp_path, monkeypatch, value):
    """With AID_STAND_IN=azure_openai or foundry, reader "llm" reads through the service behind the guard, the document names the
    service and the deployment, and no model is asked for a decision. Azure OpenAI's client gives the recorded answers of the tests,
    and Foundry's is the real one over a stub of the SDK's AnthropicFoundry: no call."""
    class Replayed(RecordedClient):                  # the recorded answers of the tests in the client's place: no call
        def __init__(self, *, deployment, **others):
            super().__init__(RECORDED_DIR)
            self.name = f"azure_openai:{deployment}"
    monkeypatch.setattr(exploration, "AzureOpenAIClient", Replayed)
    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(AnthropicFoundry=StubFoundry, DefaultHttpxClient=dict))
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    deps = build_deps(settings, switched_on(value))
    assert isinstance(deps.decider, stand_in.MadeUpDecider)
    read = deps.reader(MADE_UP_PDF, "confirmation")
    doc = read[0] if isinstance(read, tuple) else read
    assert doc.po_reference.value == "250105" and f"{value}:dep" in doc.reader
    other = tmp_path / "MU05_conf.pdf"
    other.write_bytes(MADE_UP_PDF.read_bytes() + b"\n")
    with pytest.raises(ReaderError) as refused:
        deps.reader(other, "confirmation")
    assert str(refused.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])


def test_setup_kind_names_each_setup_refusal():
    """The kind of each setup refusal of the stand-in and of the exploration readers, whatever its placeholders hold."""
    for value, service in exploration.SERVICES.items():
        for variable in ("AZURE_OPENAI_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL",
                         "ANTHROPIC_FOUNDRY_RESOURCE or ANTHROPIC_FOUNDRY_BASE_URL, not both", ENDPOINT_FORM, BASE_URL_FORM):
            assert stand_in.setup_kind(exploration.NO_SETTING.format(service=service, value=value, variable=variable)) == "missing_key"
        assert stand_in.setup_kind(exploration.NO_TEST_MODE.format(service=service, value=value)) == "test_mode"
        assert stand_in.setup_kind(exploration.NO_MAILBOX.format(service=service, value=value, field="mailbox")) == "mailbox"
    assert stand_in.setup_kind(NO_FOUNDRY_LIBRARY) == "missing_library"
    assert stand_in.setup_kind(stand_in.NO_TEST_MODE) == "test_mode"
    assert stand_in.setup_kind(stand_in.NO_KEY) == "missing_key"
    assert stand_in.setup_kind(stand_in.NO_LIBRARY) == "missing_library"
    assert stand_in.setup_kind("something else") is None
    assert stand_in.setup_kind("") is None
    assert stand_in.setup_kind(exploration.SERVICE_FAILED.format(service="Azure OpenAI", kind="X")) is None


def settings_tab(tmp_path, madeup_out, monkeypatch, value="azure_openai", **values):
    from aid.tests.test_settings_tab import tab
    for name, setting in switched_on(value).items():
        monkeypatch.setenv(name, setting)
    monkeypatch.setattr(exploration, "AzureOpenAIClient", lambda *a, **k: Service("azure"))
    return tab(tmp_path, madeup_out, test_mode=True, **values)


def test_the_settings_tab_tests_azure_openai_with_its_one_message(tmp_path, madeup_out, monkeypatch):
    from aid.review.i18n import STRINGS
    from aid.tests.test_settings_tab import handler
    path, page, made = settings_tab(tmp_path, madeup_out, monkeypatch)
    assert handler(page, "test_copilot")("", "", "en") == STRINGS["en"]["settings_ai_answer"].format(answer="OK")


def test_the_settings_tab_says_the_mailbox_refusal_in_its_own_sentence(tmp_path, madeup_out, monkeypatch):
    from aid.review.i18n import STRINGS
    from aid.tests.test_settings_tab import handler
    path, page, made = settings_tab(tmp_path, madeup_out, monkeypatch, ms_tenant_id=MAILBOX["ms_tenant_id"])
    for language in ("nl", "en"):
        assert handler(page, "test_copilot")("", "", language) == STRINGS[language]["settings_ai_test:mailbox"]


def test_a_refused_key_is_said_as_a_refused_key(tmp_path, madeup_out, monkeypatch):
    from aid.review.i18n import STRINGS
    from aid.tests.test_settings_tab import handler
    path, page, made = settings_tab(tmp_path, madeup_out, monkeypatch)
    failing = Service("azure", AzureServiceError("refused", status_code=401))
    monkeypatch.setattr(exploration, "AzureOpenAIClient", lambda *a, **k: failing)
    assert handler(page, "test_copilot")("", "", "en") == STRINGS["en"]["settings_ai_test:key_refused"]


@pytest.mark.parametrize("failure", [requests.ConnectionError("made-up"), requests.ReadTimeout("made-up")])
def test_no_connection_or_a_timeout_of_azure_openai_is_said_as_a_service_not_reached(tmp_path, madeup_out, monkeypatch, failure):
    """The button "Copilot testen" asks the client itself, so the page tells the failure by the class of the client's error: Azure
    OpenAI's for no connection and for a timeout are a service not reached, as the Anthropic library's two are."""
    from aid.review.i18n import STRINGS
    from aid.tests.test_azure_client import FakeSession
    from aid.tests.test_settings_tab import handler
    path, page, made = settings_tab(tmp_path, madeup_out, monkeypatch)
    monkeypatch.setattr(exploration, "AzureOpenAIClient", lambda **given: AzureOpenAIClient(**given | {"session": FakeSession(failure)}))
    assert handler(page, "test_copilot")("", "", "en") == STRINGS["en"]["settings_ai_test:unreachable"]


ENDPOINT_FORM = "AZURE_OPENAI_ENDPOINT as an https address such as https://<resource>.openai.azure.com"
BASE_URL_FORM = "ANTHROPIC_FOUNDRY_BASE_URL as an https address such as https://<resource>.services.ai.azure.com/anthropic"


@pytest.mark.parametrize("endpoint", [
    "http://made-up.openai.azure.com", "made-up.openai.azure.com", "https://", "https:///openai/v1",
    "https://made-up.openai.azure.com/?api-version=2025-04-01-preview", "https://made-up.openai.azure.com?",
    "https://made-up.openai.azure.com/openai/responses?api-version=2025-04-01-preview",
    "https://made-up.openai.azure.com/openai/deployments/dep/chat/completions?api-version=2025-04-01-preview",
    "https://made-up.openai.azure.com/openai", "https://made-up.openai.azure.com/openai/v1/responses",
    "https://made-up.openai.azure.com#made-up"])
def test_an_endpoint_that_is_not_the_https_address_of_the_resource_is_refused_without_its_value(made, endpoint):
    """An address over http, one with a query (a target address of the portal), or one with a path but /openai/v1: the key would leave
    the resource unencrypted, or the route would not work. No form is repaired."""
    with pytest.raises(ValueError) as refused:
        exploration.exploration_client("azure_openai", SET["azure_openai"] | {"AZURE_OPENAI_ENDPOINT": endpoint})
    assert str(refused.value) == exploration.NO_SETTING.format(service="Azure OpenAI", value="azure_openai", variable=ENDPOINT_FORM)
    assert "made-up" not in str(refused.value) and stand_in.setup_kind(str(refused.value)) == "missing_key" and made == []


def test_the_endpoint_is_taken_bare_with_a_slash_or_with_the_v1_path_in_any_case(made):
    for endpoint in ("https://made-up.openai.azure.com", "https://made-up.openai.azure.com/", "https://made-up.openai.azure.com/openai/v1",
                     " https://made-up.openai.azure.com/OpenAI/V1/ \n"):
        client = exploration.exploration_client("azure_openai", SET["azure_openai"] | {"AZURE_OPENAI_ENDPOINT": endpoint})
        assert client.url == "https://made-up.openai.azure.com/openai/v1/responses"


@pytest.mark.parametrize("base_url", ["http://made-up-resource.services.ai.azure.com/anthropic", "made-up-resource.services.ai.azure.com/anthropic",
                                      "https:///anthropic"])
def test_a_foundry_address_that_is_no_https_address_is_refused_without_its_value(made, base_url):
    rest = {name: text for name, text in SET["foundry"].items() if name not in exploration.PLACES}
    with pytest.raises(ValueError) as refused:
        exploration.exploration_client("foundry", rest | {"ANTHROPIC_FOUNDRY_BASE_URL": base_url})
    assert str(refused.value) == exploration.NO_SETTING.format(service=exploration.SERVICES["foundry"], value="foundry", variable=BASE_URL_FORM)
    assert "made-up" not in str(refused.value) and stand_in.setup_kind(str(refused.value)) == "missing_key" and made == []
    url = "https://made-up-resource.services.ai.azure.com/anthropic"
    assert exploration.exploration_client("foundry", rest | {"ANTHROPIC_FOUNDRY_BASE_URL": url})._base_url == url
