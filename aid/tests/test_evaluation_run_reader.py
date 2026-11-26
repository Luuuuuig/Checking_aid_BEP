"""The exploration readers in the timed evaluation run (the owner's decision of 26 November 2026): in the aid itself
(stand_in.stand_in_for) Azure OpenAI and Foundry also send a document whose sha256 stands in the manifest of the unseen cases, as the
stand-in for Copilot does; the one-document check (aid/readers/explore.py) and the bake-off (aid/evalscripts/bakeoff.py) never do.
Every test uses a manifest of its own in a temporary folder, never the documents of the evaluation, and no test calls a service."""
import hashlib
from types import SimpleNamespace

import pytest

from aid.evalscripts import bakeoff
from aid.readers import exploration, explore, llm_client, stand_in
from aid.readers.llm_client import ReaderError
from aid.tests.test_exploration import EXPLORATION, Service, switched_on
from aid.tests.test_runner import settings_file
from aid.tests.test_stand_in import MADE_UP_PDF, ON, Recording, request

OWN = b"%PDF-1.4 a made-up document of the test's own manifest\n"     # stands in the test's manifest
FOREIGN = b"%PDF-1.4 a document in neither set\n"                    # neither a made-up file nor in the manifest


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    """A manifest of the test's own that holds the sha256 of OWN, put in the place of the package's manifest as the default of
    made_up_digests, which both the stand-in and the guard of the aid take."""
    path = tmp_path / "made_up_extra.sha256"
    path.write_text(hashlib.sha256(OWN).hexdigest() + "\n", encoding="ascii")
    monkeypatch.setattr(stand_in, "EXTRA_MANIFEST", path)
    monkeypatch.setattr(stand_in.made_up_digests, "__defaults__", (stand_in.MADE_UP_FOLDERS, path))
    return path


@pytest.fixture
def services(monkeypatch):
    """Each client of a service that exploration_client makes, a Service that records what it is sent; Foundry's library made up."""
    made = []

    def make(**given):
        made.append(Service(f"{'foundry' if 'resource' in given else 'azure_openai'}:{given['deployment']}"))
        return made[-1]
    monkeypatch.setattr(exploration, "AzureOpenAIClient", make)
    monkeypatch.setattr(exploration, "FoundryClient", make)
    monkeypatch.setattr(llm_client, "anthropic", SimpleNamespace(AnthropicFoundry=object))
    return made


@pytest.mark.parametrize("value", EXPLORATION)
def test_in_the_aid_a_document_of_the_manifest_reaches_the_service(tmp_path, manifest, services, value):
    """a. Through stand_in_for, in the test mode, a document of the manifest goes out, as a made-up file does; a document in neither
    set and an empty one are refused before the client is called."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    guard = stand_in.stand_in_for(settings, switched_on(value))
    assert len(services) == 1 and guard.name == f"{value}:dep"
    guard.complete(request(pdf=OWN))
    guard.complete(request(pdf=MADE_UP_PDF.read_bytes()))
    assert [req.pdf for req in services[0].requests] == [OWN, MADE_UP_PDF.read_bytes()]
    for refused in (FOREIGN, OWN + b"\n", b""):
        with pytest.raises(ReaderError) as error:
            guard.complete(request(pdf=refused))
        assert str(error.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
    assert len(services[0].requests) == 2


@pytest.mark.parametrize("value", EXPLORATION)
def test_the_check_and_the_bake_off_never_send_a_document_of_the_manifest(manifest, services, value):
    """b. The guard as explore.py and bakeoff.py call it, and the readers their own functions build, refuse a document of the
    manifest before the client is called, and still send a made-up file."""
    readers = [exploration.guarded(value, Service(f"{value}:dep")),
               explore.default_factory(value, switched_on(value), None),
               bakeoff.client_for_model(f"{value}:dep", None, switched_on(value))]
    for reader in readers:
        with pytest.raises(ReaderError) as error:
            reader.complete(request(pdf=OWN))
        assert str(error.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
        assert reader.client.requests == []
        reader.complete(request(pdf=MADE_UP_PDF.read_bytes()))
        assert len(reader.client.requests) == 1
        assert hashlib.sha256(OWN).hexdigest() not in reader.allowed


@pytest.mark.parametrize("value", EXPLORATION)
def test_outside_the_test_mode_the_refusal_for_the_test_mode_comes_first(tmp_path, manifest, services, value):
    """c. Outside the test mode the exploration reader is refused for the test mode, with every variable set, before a client is made."""
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=False)
    with pytest.raises(ValueError) as refused:
        stand_in.stand_in_for(settings, switched_on(value))
    assert str(refused.value) == exploration.NO_TEST_MODE.format(service=exploration.SERVICES[value], value=value)
    assert services == []


def test_anthropic_sends_a_document_of_the_manifest_as_before(tmp_path, manifest, monkeypatch):
    """d. AID_STAND_IN=anthropic keeps its allowed set, with the manifest, its texts and its refusal of any other document."""
    monkeypatch.setattr(stand_in, "AnthropicClient", Recording)
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    guard = stand_in.stand_in_for(settings, ON)
    guard.complete(request(pdf=OWN))
    with pytest.raises(ReaderError) as refused:
        guard.complete(request(pdf=FOREIGN))
    assert str(refused.value) == stand_in.NOT_MADE_UP and [req.pdf for req in guard.client.requests] == [OWN]
    assert guard.allowed == stand_in.made_up_digests() and (guard.not_made_up, guard.service_failed) == (
        stand_in.NOT_MADE_UP, stand_in.SERVICE_FAILED)
    settings_off, _ = settings_file(tmp_path, reader="llm", test_mode=False)
    with pytest.raises(ValueError) as refused:
        stand_in.stand_in_for(settings_off, ON)
    assert str(refused.value) == stand_in.NO_TEST_MODE


@pytest.mark.parametrize("value", EXPLORATION)
def test_in_the_aid_the_reader_chosen_on_the_settings_tab_sends_a_document_of_the_manifest(tmp_path, manifest, services, monkeypatch,
                                                                                           value):
    """e. The reader that the runner builds of the service chosen on the settings tab (build_deps with reader azure_openai or foundry,
    through settings_client) is guarded as the reader of AID_STAND_IN is, so that the evaluation run works the way the owner now
    works: a document of the manifest goes out, as a made-up file does, and a document in neither set is refused before the client
    is called. The page's test of the choice (settings_client as the page calls it) and the one-document check keep the manifest
    out."""
    from aid import runner
    from aid.tests.test_exploration import chosen, keyed
    for name in exploration.PLACES:                              # the Anthropic library reads Foundry's addresses as well
        monkeypatch.delenv(name, raising=False)
    built = []
    monkeypatch.setattr(runner, "LazySecretStore", lambda directory: keyed(value))       # the store of the tab, with the key
    monkeypatch.setattr(runner, "llm_reader", lambda client: built.append(client) or (lambda path, kind: None))
    settings = chosen(tmp_path, value)
    deps = runner.build_deps(settings, {})
    [guard] = built
    assert isinstance(deps.decider, stand_in.MadeUpDecider) and guard.name == f"{value}:dep"
    guard.complete(request(pdf=OWN))
    guard.complete(request(pdf=MADE_UP_PDF.read_bytes()))
    assert [req.pdf for req in services[0].requests] == [OWN, MADE_UP_PDF.read_bytes()]
    with pytest.raises(ReaderError) as error:
        guard.complete(request(pdf=FOREIGN))
    assert str(error.value) == exploration.NOT_MADE_UP.format(service=exploration.SERVICES[value])
    assert guard.allowed == stand_in.made_up_digests() and hashlib.sha256(OWN).hexdigest() in guard.allowed
    page_choice = exploration.settings_client(value, settings, keyed(value), environ={})      # as the page calls it
    for other in (page_choice, explore.default_factory(value, switched_on(value), None)):
        with pytest.raises(ReaderError):
            other.complete(request(pdf=OWN))
        assert other.client.requests == [] and hashlib.sha256(OWN).hexdigest() not in other.allowed
