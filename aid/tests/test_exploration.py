"""The exploration readers (aid/readers/exploration.py): Azure OpenAI and Anthropic's models in Microsoft Foundry in Copilot's place
while the process has AID_STAND_IN=azure_openai or foundry, or the settings tab chose one as the reader, for the made-up documents
only (in the aid also those of the unseen cases, never in the one-document check or the bake-off) and in the test mode only, which
is the one switch: a mailbox in the settings is no refusal (the owner's decision of 26 November). No test calls a service."""
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
# A value that each of the five settings of a mailbox takes in a settings file (aid/settings.py), made up.
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
        assert guard.name == f"{value}:dep" and guard.allowed == stand_in.made_up_digests()
        assert stand_in.extra_digests() and stand_in.extra_digests() <= guard.allowed      # in the aid, the unseen cases too (26 November)


def test_anthropic_is_unchanged(tmp_path, monkeypatch):
    """AID_STAND_IN=anthropic keeps its allowed set, with the manifest of the unseen cases, and its texts, and it is not refused while
    the settings name every part of a mailbox."""
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
    """Outside the test mode the exploration readers are refused first, before the variables are looked at, also with every part of
    a mailbox in the settings."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=False, **MAILBOX)
    with pytest.raises(ValueError) as refused:
        build_deps(settings, {"AID_STAND_IN": value})
    assert str(refused.value) == exploration.NO_TEST_MODE.format(service=exploration.SERVICES[value], value=value)
    assert made == []


MAILBOX_PARTS = ("ms_tenant_id", "ms_client_id", "mailbox", "mail_folder", "order_folder")      # written out, not taken from the code


@pytest.mark.parametrize("value", EXPLORATION)
@pytest.mark.parametrize("field", MAILBOX_PARTS)
def test_in_the_test_mode_the_reader_is_built_with_any_setting_of_a_mailbox_on_both_paths(tmp_path, made, value, field):
    """The owner's decision of 26 November: the test mode is the one switch, and the refusal of a mailbox is gone. With each of the
    five settings of a mailbox set, and with all five, the reader is built in the test mode on both paths, that of AID_STAND_IN and
    that of the settings tab (settings_client), behind the guard of the made-up folders of the package, which the reader of
    AID_STAND_IN in the aid opens to the manifest of the unseen cases as well (the evaluation run). A settings file holds order_folder
    only beside mail_folder, so each field is set alone on a copy of the settings."""
    assert tuple(MAILBOX) == MAILBOX_PARTS
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    (tmp_path / "tab").mkdir()
    on_the_tab = chosen(tmp_path / "tab", value)
    for given in ({field: MAILBOX[field]}, {part: MAILBOX[part] for part in MAILBOX_PARTS}):
        aid_stand_in = stand_in.stand_in_for(settings.model_copy(update=given), switched_on(value))
        tab_choice = exploration.settings_client(value, on_the_tab.model_copy(update=given), keyed(value), environ=NO_PLACE)
        assert aid_stand_in.name == tab_choice.name == f"{value}:dep"
        assert aid_stand_in.allowed == stand_in.made_up_digests()            # in the aid, the unseen cases too (26 November)
        assert tab_choice.allowed == stand_in.made_up_digests(manifest=None)  # as the page's test of the choice builds it
    assert len(made) == 4
    assert not any(hasattr(exploration, name) for name in ("MAILBOX_FIELDS", "check_no_mailbox", "NO_MAILBOX"))     # the refusal is gone


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


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_reader_of_the_settings_tab_takes_its_key_from_the_store_only(tmp_path, made, monkeypatch, value):
    """The tab's path mirrors the test above: the key of reader "azure_openai" or "foundry" comes from the secrets store of this PC
    only. With no key in the store, a key under the secret's name or under the variable's name in a .env file, in a file named
    after either, in the folder's settings file (as the value of a field the file allows) or in the process environment is never
    taken: settings_client, and the runner that builds it, are refused with the refusal of a missing key, and the key is in no
    text of it."""
    from aid.connections.secrets import SecretStore
    variable = next(name for name in exploration.VARIABLES[value] if name.endswith("_API_KEY"))
    secret, key = SECRET[value], "made-up-key-in-a-file"
    for name in (variable, secret):
        monkeypatch.setenv(name, key)
    for name in exploration.PLACES:
        monkeypatch.delenv(name, raising=False)
    settings = chosen(tmp_path, value, exact_fields={variable: key, secret: key})
    assert settings.exact_fields == {variable: key, secret: key}
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text(f"{variable}={key}\n{secret}={key}\n", encoding="utf-8")
    for name in (variable, secret):
        (work / name).write_text(key + "\n", encoding="utf-8")
    monkeypatch.chdir(work)
    expected = exploration.READER_KEY.format(value=value, service=exploration.SERVICES[value], setting=secret)
    for store in ({}, SecretStore(tmp_path / "default-secrets")):      # no key: an empty store, and the empty store of this PC
        with pytest.raises(exploration.SettingsRefused) as refused:
            exploration.settings_client(value, settings, store)
        assert (refused.value.kind, refused.value.settings, str(refused.value)) == ("key", (secret,), expected)
        assert key not in str(refused.value)
    with pytest.raises(exploration.SettingsRefused) as refused:
        build_deps(settings, os.environ)
    assert str(refused.value) == expected and key not in str(refused.value) and made == []


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
    """The allowed set of an exploration reader as the one-document check and the bake-off build it is the made-up folders of the
    package, without the manifest of the unseen cases that the aid takes as well; checked here also where evaluation/unseen_cases is
    not in the repository."""
    guard = exploration.guarded(value, Service(f"{value}:dep"))
    assert guard.allowed and guard.allowed == stand_in.made_up_digests(manifest=None)
    assert stand_in.extra_digests() and not stand_in.extra_digests() & guard.allowed
    assert guard.allowed | stand_in.extra_digests() == stand_in.made_up_digests()


@pytest.mark.skipif(not UNSEEN_DOCUMENTS.is_dir(), reason="evaluation/unseen_cases is not in this repository")
def test_a_document_of_the_unseen_cases_goes_to_anthropic_but_not_to_the_exploration_readers(tmp_path, monkeypatch):
    """The exploration readers here are guarded as the one-document check and the bake-off guard them; in the aid they take the
    manifest as well (test_evaluation_run_reader.py)."""
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


def test_the_settings_tab_tests_the_service_of_aid_stand_in_with_a_mailbox_in_the_settings(tmp_path, madeup_out, monkeypatch):
    """In the test mode a mailbox in the settings is no refusal: "Copilot testen" sends the one test message through the service, and
    the page has no sentence for a refusal of a mailbox any more."""
    from aid.review.i18n import STRINGS
    from aid.tests.test_settings_tab import handler
    path, page, made = settings_tab(tmp_path, madeup_out, monkeypatch, ms_tenant_id=MAILBOX["ms_tenant_id"],
                                    ms_client_id=MAILBOX["ms_client_id"], mail_folder=MAILBOX["mail_folder"])
    for language in ("nl", "en"):
        assert handler(page, "test_copilot")("", "", language) == STRINGS[language]["settings_ai_answer"].format(answer="OK")
        assert not [key for key in STRINGS[language] if key.endswith(":mailbox") and key.startswith(("settings_ai_test", "service_why"))]


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


# --- the readers the settings tab chooses: reader "azure_openai" or "foundry", their settings, and their keys in the secrets store ---

KEY = "made-up-key-0123456789"
# The settings of each service as the settings tab saves them, made up, and its key in the secrets store.
FIELDS = {"azure_openai": {"azure_openai_endpoint": "https://made-up.openai.azure.com", "azure_openai_deployment": "dep"},
          "foundry": {"foundry_resource": "made-up-resource", "foundry_deployment": "dep"}}
SECRET = {"azure_openai": "azure_openai_api_key", "foundry": "foundry_api_key"}
NO_PLACE: dict[str, str] = {}                                   # a process environment without either Foundry address


def chosen(tmp_path, value: str, **values):
    """The settings of a made-up folder with the reader `value` and its fields, in the test mode."""
    settings, _ = settings_file(tmp_path, **{"reader": value, "test_mode": True} | FIELDS[value] | values)
    return settings


def keyed(value: str) -> dict[str, str]:
    """A store that holds the key of the service of `value`: the settings tab's, as get reads it."""
    return {SECRET[value]: KEY}


def test_the_names_of_the_keys_in_the_store():
    assert exploration.KEYS == SECRET and set(exploration.KEYS) == set(exploration.SERVICES)


def test_store_key_keeps_a_key_and_gives_what_puts_the_store_back():
    """The settings tab keeps a key through store_key, which gives what undoes it, so that a save whose write of the fields fails
    puts the store back as it was: the key that was there before, or none; the tab never holds the key that was there."""
    class Store(dict):
        def set(self, name, key):
            self[name] = key

        def delete(self, name):
            self.pop(name, None)
    for before in ({}, {SECRET["foundry"]: "made-up-key-old"}):
        store = Store(before | {SECRET["azure_openai"]: "made-up-key-other"})
        undo = exploration.store_key("foundry", store, KEY)
        assert store[SECRET["foundry"]] == KEY
        undo()
        assert store == before | {SECRET["azure_openai"]: "made-up-key-other"}


def test_key_stored_says_only_whether_the_store_holds_a_key():
    """The settings tab asks whether a key is stored through key_stored, which gives a bool and never the key, so that only the
    builder of the reader (settings_client) holds a key; a blank one counts as none, as settings_client takes it."""
    for value in EXPLORATION:
        assert exploration.key_stored(value, keyed(value)) is True
        assert exploration.key_stored(value, {}) is False and exploration.key_stored(value, {SECRET[value]: " \n"}) is False
    assert exploration.key_stored("foundry", keyed("azure_openai")) is False


@pytest.mark.parametrize("value", EXPLORATION)
def test_settings_client_builds_the_guarded_client_of_the_settings_and_the_store(tmp_path, made, value):
    """The client of the service is made from the settings, each value without the spaces around it, and the key the store holds
    under its name; it is the client behind the guard of the made-up folders of the package, with the effort of the settings for
    Foundry and the test text given. The process environment holds none of the service's variables, and the client takes none."""
    settings = chosen(tmp_path, value, effort="high")
    if value == "azure_openai":
        settings = settings.model_copy(update={"azure_openai_endpoint": " https://made-up.openai.azure.com/ \n"})
    guard = exploration.settings_client(value, settings, {SECRET[value]: f" {KEY} \n"}, test_text=TEST_TEXT, environ=NO_PLACE)
    assert isinstance(guard, stand_in.StandIn) and guard.test_text == TEST_TEXT and guard.name == f"{value}:dep"
    assert guard.allowed == stand_in.made_up_digests(manifest=None)
    assert guard.not_made_up == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
    [client] = made
    assert client is guard.client and client._api_key == KEY
    if value == "azure_openai":
        assert (client.url, client.deployment) == ("https://made-up.openai.azure.com/openai/v1/responses", "dep")
    else:
        assert (client._resource, client._base_url, client.model, client.effort) == ("made-up-resource", None, "dep", "high")
    base = exploration.settings_client("foundry", chosen(tmp_path, "foundry", foundry_resource=None,
                                                          foundry_base_url="https://made-up.services.ai.azure.com/anthropic"),
                                       keyed("foundry"), environ=NO_PLACE)
    assert (base.client._resource, base.client._base_url, base.test_text) == (None, "https://made-up.services.ai.azure.com/anthropic", None)


@pytest.mark.parametrize("value", EXPLORATION)
def test_settings_client_is_refused_outside_the_test_mode_before_the_key_is_read_and_built_with_a_mailbox_in_it(tmp_path, made, value):
    """Outside the test mode the reader is refused before the store is asked for the key, also with every part of a mailbox in the
    settings: a refusal that names the setting and the reader, and never a value. In the test mode, which is the one switch, each of
    the five settings of a mailbox, and all five, are no refusal (the owner's decision of 26 November)."""
    class Untouched(dict):
        def get(self, name, default=None):
            raise AssertionError("the store was asked")
    for settings in (chosen(tmp_path, value, test_mode=False), chosen(tmp_path, value, test_mode=False).model_copy(update=MAILBOX)):
        with pytest.raises(exploration.SettingsRefused) as refused:
            exploration.settings_client(value, settings, Untouched(), environ=NO_PLACE)
        assert (refused.value.kind, refused.value.settings) == ("test_mode", ("test_mode",))
        assert str(refused.value) == exploration.READER_TEST_MODE.format(value=value, service=exploration.SERVICES[value])
    assert made == []
    for given in [*({field: MAILBOX[field]} for field in MAILBOX_PARTS), MAILBOX]:
        guard = exploration.settings_client(value, chosen(tmp_path, value).model_copy(update=given), keyed(value), environ=NO_PLACE)
        assert guard.name == f"{value}:dep" and guard.allowed == stand_in.made_up_digests(manifest=None)
    assert len(made) == 6


@pytest.mark.parametrize("value, missing, kind, named", [
    ("azure_openai", "azure_openai_endpoint", "missing", ("azure_openai_endpoint",)),
    ("azure_openai", "azure_openai_deployment", "missing", ("azure_openai_deployment",)),
    ("azure_openai", "key", "key", ("azure_openai_api_key",)),
    ("foundry", "foundry_deployment", "missing", ("foundry_deployment",)),
    ("foundry", "foundry_resource", "missing", ("foundry_resource", "foundry_base_url")),
    ("foundry", "key", "key", ("foundry_api_key",)),
])
def test_settings_client_names_the_setting_or_the_key_that_is_missing(tmp_path, made, value, missing, kind, named):
    """A setting that is null, empty or holds spaces only, or a key that the store does not hold or holds blank, is missing: the
    refusal names the setting, or the key by its name in the store, and never a value."""
    for blank in (None, "", " \n"):
        settings, store = chosen(tmp_path, value), keyed(value)
        if missing == "key":
            store = {} if blank is None else {SECRET[value]: blank}
        else:
            settings = settings.model_copy(update={missing: blank})
        with pytest.raises(exploration.SettingsRefused) as refused:
            exploration.settings_client(value, settings, store, environ=NO_PLACE)
        assert (refused.value.kind, refused.value.settings, refused.value.variable) == (kind, named, None)
        assert all(name in str(refused.value) for name in named) and KEY not in str(refused.value)
        assert "made-up" not in str(refused.value) and "AZURE_OPENAI" not in str(refused.value) and "ANTHROPIC" not in str(refused.value)
    assert made == []


@pytest.mark.parametrize("value, setting, address, example", [
    ("azure_openai", "azure_openai_endpoint", "http://made-up.openai.azure.com", "https://<resource>.openai.azure.com"),
    ("azure_openai", "azure_openai_endpoint", "https://made-up.openai.azure.com/openai/deployments/dep?api-version=1", "https://<resource>.openai.azure.com"),
    ("foundry", "foundry_base_url", "http://made-up.services.ai.azure.com/anthropic", "https://<resource>.services.ai.azure.com/anthropic"),
])
def test_settings_client_refuses_an_address_in_another_form_by_its_setting(tmp_path, made, value, setting, address, example):
    """The forms are those of the environment's path (exploration_client): an address in another form is refused by the setting and
    the form it needs, never repaired and never named by its value."""
    settings = chosen(tmp_path, value, **{setting: address} | ({"foundry_resource": None} if value == "foundry" else {}))
    with pytest.raises(exploration.SettingsRefused) as refused:
        exploration.settings_client(value, settings, keyed(value), environ=NO_PLACE)
    assert (refused.value.kind, refused.value.settings) == ("form", (setting,))
    assert str(refused.value) == exploration.READER_FORM.format(value=value, service=exploration.SERVICES[value], setting=setting,
                                                                example=example)
    assert "made-up" not in str(refused.value) and made == []


def test_settings_client_takes_the_foundry_resource_or_its_address_and_not_both(tmp_path, made):
    """Both Foundry addresses in the settings are refused by their names; one of them beside the other one in the process
    environment is refused as well, even an empty one, since the Anthropic library reads both from the environment and would
    refuse the pair at every request. The same one in the environment is no conflict: the client gives its own."""
    url = "https://made-up.services.ai.azure.com/anthropic"
    both = chosen(tmp_path, "foundry", foundry_base_url=url)
    with pytest.raises(exploration.SettingsRefused) as refused:
        exploration.settings_client("foundry", both, keyed("foundry"), environ=NO_PLACE)
    assert (refused.value.kind, refused.value.settings) == ("both", ("foundry_resource", "foundry_base_url"))
    assert "made-up" not in str(refused.value)
    by_url = chosen(tmp_path, "foundry", foundry_resource=None, foundry_base_url=url)
    for settings, given, other, instead in ((chosen(tmp_path, "foundry"), "foundry_resource", "ANTHROPIC_FOUNDRY_BASE_URL", "foundry_base_url"),
                                            (by_url, "foundry_base_url", "ANTHROPIC_FOUNDRY_RESOURCE", "foundry_resource")):
        for held in ("https://elsewhere.example.com/anthropic", ""):
            with pytest.raises(exploration.SettingsRefused) as refused:
                exploration.settings_client("foundry", settings, keyed("foundry"), environ={other: held})
            assert (refused.value.kind, refused.value.settings, refused.value.variable) == ("environment", (given,), other)
            assert str(refused.value) == exploration.READER_ENVIRONMENT.format(service=exploration.SERVICES["foundry"], setting=given,
                                                                               variable=other, other=instead)
            assert "elsewhere" not in str(refused.value) and KEY not in str(refused.value)
    same = exploration.settings_client("foundry", chosen(tmp_path, "foundry"), keyed("foundry"),
                                       environ={"ANTHROPIC_FOUNDRY_RESOURCE": "elsewhere"})
    assert same.client._resource == "made-up-resource"


def test_settings_client_takes_the_process_environment_by_default(tmp_path, made, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_BASE_URL", "")
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_RESOURCE", raising=False)
    with pytest.raises(exploration.SettingsRefused) as refused:
        exploration.settings_client("foundry", chosen(tmp_path, "foundry"), keyed("foundry"))
    assert (refused.value.kind, refused.value.variable) == ("environment", "ANTHROPIC_FOUNDRY_BASE_URL")
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL")
    assert exploration.settings_client("foundry", chosen(tmp_path, "foundry"), keyed("foundry")).name == "foundry:dep"


def test_settings_client_needs_the_library_s_client_for_foundry(tmp_path, monkeypatch, made):
    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(Anthropic=object))
    with pytest.raises(ValueError) as refused:
        exploration.settings_client("foundry", chosen(tmp_path, "foundry"), keyed("foundry"), environ=NO_PLACE)
    assert str(refused.value) == NO_FOUNDRY_LIBRARY and stand_in.setup_kind(str(refused.value)) == "missing_library" and made == []


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_guard_still_refuses_a_document_that_is_not_made_up_through_the_tab_s_path(tmp_path, monkeypatch, value):
    """The reader of the service the settings tab chooses, as the tab's own test builds it, sends only the made-up files of the
    package, no document of the unseen cases (its allowed set holds none of the manifest's), and a request without a document goes
    out only as the test message. The runner's reader of that choice takes the manifest as well (unseen_cases), as the reader of
    AID_STAND_IN does in the aid, for the evaluation run (test_evaluation_run_reader.py)."""
    fake = Service(f"{value}:dep")
    monkeypatch.setattr(exploration, "exploration_client", lambda *args, **kwargs: fake)
    guard = exploration.settings_client(value, chosen(tmp_path, value), keyed(value), test_text=TEST_TEXT, environ=NO_PLACE)
    assert guard.allowed == stand_in.made_up_digests(manifest=None)
    in_the_runner = exploration.settings_client(value, chosen(tmp_path, value), keyed(value), environ=NO_PLACE, unseen_cases=True)
    assert in_the_runner.allowed == stand_in.made_up_digests() and stand_in.extra_digests() <= in_the_runner.allowed
    pdf = MADE_UP_PDF.read_bytes()
    for refused in (request(pdf=pdf + b"\n"), request(images=[b"\x89PNG made-up"]), request()):
        with pytest.raises(ReaderError) as error:
            guard.complete(refused)
        assert str(error.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
    assert fake.requests == []
    guard.complete(request(pdf=pdf))
    assert guard.ask(TEST_TEXT).text == "OK" and len(fake.requests) == 2
    with pytest.raises(ReaderError):
        guard.ask("Tell me about the order 250105.")


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_runner_reads_through_the_reader_the_settings_tab_chose(tmp_path, monkeypatch, value):
    """With reader "azure_openai" or "foundry" the runner reads through settings_client's guarded reader, made with the key of the
    secrets store of this PC (default_secrets_dir, a folder of the test here), whatever AID_STAND_IN says, and no model is asked for
    a decision (MadeUpDecider). A document that is not made up is refused before anything is sent."""
    from aid.connections.secrets import SecretStore

    class Replayed(RecordedClient):                  # the recorded answers of the tests in the client's place: no call
        def __init__(self, *, deployment, api_key, **others):
            super().__init__(RECORDED_DIR)
            self.name, self.key = f"azure_openai:{deployment}", api_key
    monkeypatch.setattr(exploration, "AzureOpenAIClient", Replayed)
    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(AnthropicFoundry=StubFoundry, DefaultHttpxClient=dict))
    for name in exploration.PLACES:
        monkeypatch.delenv(name, raising=False)
    SecretStore(tmp_path / "default-secrets").set(SECRET[value], KEY)
    for environ in ({}, {"AID_STAND_IN": "anthropic"}, switched_on("azure_openai" if value == "foundry" else "foundry")):
        deps = build_deps(chosen(tmp_path, value), environ)
        assert not [name for name, text in os.environ.items() if KEY in text]          # the key goes into no environment
        assert isinstance(deps.decider, stand_in.MadeUpDecider)
        read = deps.reader(MADE_UP_PDF, "confirmation")
        doc = read[0] if isinstance(read, tuple) else read
        assert doc.po_reference.value == "250105" and f"{value}:dep" in doc.reader
    other = tmp_path / "MU05_conf.pdf"
    other.write_bytes(MADE_UP_PDF.read_bytes() + b"\n")
    with pytest.raises(ReaderError) as refused:
        deps.reader(other, "confirmation")
    assert str(refused.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_runner_does_not_start_while_the_reader_the_settings_tab_chose_is_refused(tmp_path, capsys, made, value):
    """A refusal of settings_client stops the start of the runner with exit code 2, as a refused stand-in does: its sentence names
    the setting or the key, never a value, and nothing is sent."""
    from aid.connections.secrets import SecretStore
    from aid.runner import main
    _, path = settings_file(tmp_path, **{"reader": value, "test_mode": True} | FIELDS[value])
    assert main(["--settings", str(path), "--max-passes", "1"]) == 2
    err = capsys.readouterr().err
    expected = exploration.READER_KEY.format(value=value, service=exploration.SERVICES[value], setting=SECRET[value])
    assert err.splitlines()[-1] == f"aid.runner: {expected}"              # after the store's line, where it keeps plain files
    SecretStore(tmp_path / "default-secrets").set(SECRET[value], KEY)
    _, path = settings_file(tmp_path, **{"reader": value, "test_mode": False} | FIELDS[value])
    assert main(["--settings", str(path), "--max-passes", "1"]) == 2
    err = capsys.readouterr().err
    assert "test mode" in err and KEY not in err and made == []
    deployment = f"{value}_deployment"                    # a refusal after the key was read (the review's keys-4)
    _, path = settings_file(tmp_path, **{"reader": value, "test_mode": True} | FIELDS[value] | {deployment: ""})
    assert main(["--settings", str(path), "--max-passes", "1"]) == 2
    err = capsys.readouterr().err
    assert err.splitlines()[-1] == "aid.runner: " + exploration.READER_SETTING.format(value=value, service=exploration.SERVICES[value],
                                                                                       setting=deployment)
    assert KEY not in err and made == [] and not [name for name, text in os.environ.items() if KEY in text]


@pytest.mark.parametrize("value", EXPLORATION)
@pytest.mark.parametrize("attachment", ["a file of the package", "a scan of the package", "another file", "another image"])
def test_through_a_mailbox_only_a_file_of_the_package_is_sent_and_any_other_attachment_fails_unread(tmp_path, madeup_out, monkeypatch,
                                                                                                  value, attachment):
    """The owner's decision of 26 November: in the test mode the runner reads a mailbox of one's own, to which only made-up mails are sent,
    and the documents through the service in one run, and the guard stays as it is. Through the mailbox intake (Graph faked, two
    passes of the runner: the baseline, then the new message) an attachment that is, byte for byte, a made-up file of the package is
    sent, its own bytes and nothing of the e-mail; any other attachment is refused before anything leaves the PC, the service is asked
    nothing, and its case fails at once as one whose document Copilot could not read. A made-up scan of the package, which has no
    text, is classed as a confirmation by its name and read through the service, its own bytes only. An image that is no file of the
    package is classed by no model either: it opens no case and is left to the buyer, and nothing is sent (the review's guard-2).
    No model is asked to class a document."""
    from aid.connections.secrets import SecretStore
    from aid.runner import run_loop
    from aid.tests.fakes import FakeResponse, FakeSession
    from aid.review.intake import recent
    from aid.tests.test_microsoft_mail import (DELTA_1, DELTA_2, baseline, confirmation_email, mail_deps, mail_settings, message, page,
                                               signed_in_account, value_url)
    sent = []

    class Recording(RecordedClient):                 # the recorded answers of the tests in the service's place: it keeps each request
        def __init__(self, *, deployment, **others):
            super().__init__(RECORDED_DIR)
            self.name = f"{value}:{deployment}"

        def complete(self, req):
            sent.append(req)
            return super().complete(req)
    monkeypatch.setattr(exploration, "AzureOpenAIClient", Recording)
    monkeypatch.setattr(exploration, "FoundryClient", Recording)
    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(AnthropicFoundry=object, DefaultHttpxClient=dict))
    for name in exploration.PLACES:
        monkeypatch.delenv(name, raising=False)
    SecretStore(tmp_path / "default-secrets").set(SECRET[value], KEY)
    settings = mail_settings(tmp_path, reader=value, test_mode=True, **FIELDS[value])
    assert all(getattr(settings, name) for name in ("ms_tenant_id", "ms_client_id", "mail_folder"))      # the mailbox is read
    built = build_deps(settings, {})
    assert isinstance(built.decider, stand_in.MadeUpDecider)
    d = mail_deps(tmp_path, madeup_out).model_copy(update={"settings": settings, "reader": built.reader, "decider": built.decider})
    name = {"a scan of the package": "MU05_conf.pdf", "another image": "CART02.png"}.get(attachment, "MU01_conf.pdf")
    package = stand_in.MADE_UP_FOLDERS[0] / name
    document = package.read_bytes() if attachment.endswith("of the package") else package.read_bytes() + b"\n"
    mailed = tmp_path / "mailed" / name
    mailed.parent.mkdir()
    mailed.write_bytes(document)
    email = confirmation_email(mailed)
    if attachment == "another image":                           # the same e-mail, with a PNG image in place of the PDF
        email = email.replace(b"Content-Type: application/pdf", b"Content-Type: image/png")
        assert b"Content-Type: image/png" in email
    session = FakeSession({("GET", baseline()): page([], delta_link=DELTA_1),
                           ("GET", DELTA_1): page([message("M1", "2026-11-12T08:00:30Z")], delta_link=DELTA_2),
                           ("GET", value_url("M1")): FakeResponse(content=email)})
    root = tmp_path / "cases"
    assert run_loop(d, tmp_path / "STOP", poll_seconds=0, max_passes=2, account=signed_in_account(tmp_path), session=session) == 2
    cases = [folder for folder in root.iterdir() if (folder / "case.json").is_file()]
    if attachment == "another image":
        assert sent == [] and cases == []                       # classed by no model, so no case: it stays with the buyer
        assert [line["outcome"] for line in recent(root)] == ["skipped"]
        return
    [case] = cases
    result = json.loads((case / "steps" / "08_result.json").read_text(encoding="utf-8"))
    assert (case / "input" / name).read_bytes() == document
    assert json.loads((case / "case.json").read_text(encoding="utf-8"))["kind"] == "confirmation"
    if attachment.endswith("of the package"):
        assert [(req.pdf, req.images) for req in sent] == [(document, [])] and result["status"] != "failed"
        for words in ("Orderbevestiging", "Bijgaand", "s01@example.com", "made-up-0001"):      # nothing of the e-mail goes out
            assert not [req for req in sent if words in f"{req.system} {req.user_text}"], words
    else:
        assert sent == [] and result["status"] == "failed" and case.name.startswith("250101_")
        assert result["error"] == f"ReaderError: {exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])}"


def changed(path, **values) -> None:
    """A change made in the settings file, with a modification time that differs from the one before, as the runner notices it."""
    before = path.stat().st_mtime_ns
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | values), encoding="utf-8")
    os.utime(path, ns=(before + 2_000_000_000, before + 2_000_000_000))


def test_the_runner_logs_a_refused_reader_of_the_settings_tab_by_its_kind_and_setting(tmp_path, made):
    """A changed settings file whose reader the tab chose cannot be built (the test mode switched off by hand, a deployment emptied,
    the key gone): the runner keeps the settings in use, and its log names the kind of the refusal and the setting, never a value;
    every other error keeps its line of the class only (the review's guard-3)."""
    from aid.connections.secrets import SecretStore
    from aid.runner import LOG_FILE, _Loop
    store = SecretStore(tmp_path / "default-secrets")
    store.set(SECRET["azure_openai"], KEY)
    settings, path = settings_file(tmp_path, **{"reader": "azure_openai", "test_mode": True} | FIELDS["azure_openai"])
    (tmp_path / "cases").mkdir()
    deps = build_deps(settings, {})
    loop = _Loop(deps, path, lambda held: build_deps(held, {}))
    for change, named in (({"test_mode": False}, "test_mode"),
                          ({"test_mode": True, "azure_openai_deployment": ""}, "missing: azure_openai_deployment")):
        changed(path, **change)
        loop.reload()
        assert loop.deps is deps
        last = (tmp_path / "cases" / LOG_FILE).read_text(encoding="utf-8").splitlines()[-1]
        assert last.endswith(f" settings: SettingsRefused ({named}): the changed settings cannot be used; the settings in use are kept")
    changed(path, azure_openai_deployment="dep")
    store.delete(SECRET["azure_openai"])
    loop.reload()
    log = (tmp_path / "cases" / LOG_FILE).read_text(encoding="utf-8")
    assert log.splitlines()[-1].endswith(" settings: SettingsRefused (key: azure_openai_api_key): the changed settings cannot be used; "
                                         "the settings in use are kept")
    assert KEY not in log and "made-up" not in log and loop.deps is deps
    store.set(SECRET["azure_openai"], KEY)
    changed(path, reader="parser")
    loop.reload()
    assert loop.deps is not deps and loop.deps.reader.__name__ == "read_with_parser"
