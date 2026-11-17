"""The stand-in for Copilot (aid/readers/stand_in.py): the Anthropic model in Copilot's place, for the made-up documents only, while the process
has AID_STAND_IN=anthropic and the settings have the test mode on."""
import hashlib
import json
from email.message import EmailMessage

import pytest

from aid.decider import NoDecider
from aid.readers import llm_client, stand_in
from aid.demo import RECORDED_DIR
from aid.readers.llm_client import LlmRequest, LlmResponse, ReaderError, RecordedClient
from aid.runner import NO_LLM, build_deps
from aid.settings import Settings
from aid.tests.test_runner import settings_file

MADE_UP_PDF = stand_in.MADE_UP_FOLDERS[0] / "MU05_conf.pdf"
MADE_UP_PNG = stand_in.MADE_UP_FOLDERS[0] / "CART02.png"
ON = {"AID_STAND_IN": "anthropic", "ANTHROPIC_API_KEY": "made-up-key-for-the-test"}


class Recording:
    """A stand-in for the Anthropic client: it records each request and answers with the test's answer."""

    def __init__(self, model, effort="medium"):
        self.name, self.model, self.effort, self.requests = model, model, effort, []

    def complete(self, req):
        self.requests.append(req)
        return LlmResponse(text=json.dumps({"answer": "OK"}), model=self.name, input_tokens=1, output_tokens=1, seconds=0.0)


def request(**document) -> LlmRequest:
    return LlmRequest(system="made-up system", user_text="made-up text", schema={"type": "object"}, **document)


def test_it_is_on_only_while_the_variable_says_anthropic():
    assert stand_in.active({"AID_STAND_IN": "anthropic"}) and stand_in.active({"AID_STAND_IN": " Anthropic "})
    assert not stand_in.active({}) and not stand_in.active({"AID_STAND_IN": "1"}) and not stand_in.active({"AID_STAND_IN": ""})


def test_a_made_up_document_goes_out_and_any_other_is_refused_before_anything_is_sent():
    """Only a document that is, byte for byte, a file of the made-up set goes to the service: a PDF with one byte more, an image
    of another file, a request without a document, and a made-up PDF with a foreign image beside it are refused and never sent."""
    client = Recording("made-up-model")
    guard = stand_in.StandIn(client)
    pdf, png = MADE_UP_PDF.read_bytes(), MADE_UP_PNG.read_bytes()
    guard.complete(request(pdf=pdf))
    guard.complete(request(images=[png]))
    assert [len(req.images) for req in client.requests] == [0, 1]
    for refused in (request(pdf=pdf + b"\n"), request(images=[png[:-1]]), request(), request(pdf=pdf, images=[b"\x89PNG made-up"])):
        with pytest.raises(ReaderError, match="made-up documents only"):
            guard.complete(refused)
    assert len(client.requests) == 2 and guard.name == "made-up-model"


def test_the_one_test_message_of_the_settings_tab_goes_out_without_a_document_and_nothing_else_does():
    client = Recording("made-up-model")
    guard = stand_in.StandIn(client, test_text="Answer with the word OK only.")
    assert guard.ask("Answer with the word OK only.").text == "OK"
    for text, context in (("Tell me about the order 250105.", None), ("Answer with the word OK only.", [{"made": "up"}])):
        with pytest.raises(ReaderError):
            guard.ask(text, context)
    assert len(client.requests) == 1 and client.requests[0].pdf is None and not client.requests[0].images
    with pytest.raises(ReaderError):                 # the runner's stand-in has no test text: it asks nothing without a document
        stand_in.StandIn(Recording("made-up-model")).ask("Answer with the word OK only.")


def test_the_runner_reads_with_the_stand_in_in_the_test_mode_and_asks_no_model_for_a_decision(tmp_path, monkeypatch):
    """With AID_STAND_IN=anthropic, reader "llm" reads through the stand-in, without the tenant and the client of Copilot, and no model
    is asked for a decision, since its question holds the values of a case that the stand-in cannot tell from a company case's."""
    class Replayed(RecordedClient):                  # the recorded answers of the tests in the client's place: no call
        def __init__(self, model, effort="medium"):
            super().__init__(RECORDED_DIR)
            self.name, self.model, self.effort = model, model, effort
    monkeypatch.setattr(stand_in, "AnthropicClient", Replayed)
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    deps = build_deps(settings, ON)
    assert isinstance(deps.decider, stand_in.MadeUpDecider)
    read = deps.reader(MADE_UP_PDF, "confirmation")
    doc = read[0] if isinstance(read, tuple) else read
    assert doc.po_reference.value == "250105" and Settings.model_fields["model"].default in doc.reader     # the example's placeholder
    with pytest.raises(ReaderError, match="made-up documents only"):            # a document of no file of the set is never sent
        other = tmp_path / "MU05_conf.pdf"
        other.write_bytes(MADE_UP_PDF.read_bytes() + b"\n")
        deps.reader(other, "confirmation")
    with pytest.raises(ValueError, match=NO_LLM.split(":")[0]):                    # without the variable: Copilot's rule, as before
        build_deps(settings, {"ANTHROPIC_API_KEY": "made-up-key-for-the-test"})


@pytest.mark.parametrize("values, environ, refusal", [
    ({"test_mode": False}, ON, "test mode"),
    ({"test_mode": True}, {"AID_STAND_IN": "anthropic"}, "ANTHROPIC_API_KEY"),
])
def test_the_stand_in_is_refused_outside_the_test_mode_and_without_the_key(tmp_path, monkeypatch, values, environ, refusal):
    monkeypatch.setattr(stand_in, "AnthropicClient", Recording)
    settings, _ = settings_file(tmp_path, reader="llm", **values)
    with pytest.raises(ValueError, match=refusal):
        build_deps(settings, environ)


def test_the_stand_in_says_when_the_python_has_no_anthropic_library(tmp_path, monkeypatch):
    """The folder for Windows does without the development client's library: the stand-in then says to start the aid with a Python
    that has it, and sends nothing."""
    monkeypatch.setattr(llm_client, "anthropic", None)
    settings, _ = settings_file(tmp_path, reader="llm", test_mode=True)
    with pytest.raises(ValueError, match="Anthropic library"):
        build_deps(settings, ON)


def test_the_parser_stays_the_parser_with_the_variable_set(tmp_path):
    settings, _ = settings_file(tmp_path, reader="parser", test_mode=True)
    deps = build_deps(settings, ON)
    assert isinstance(deps.decider, NoDecider) and deps.reader.__name__ == "read_with_parser"


def test_the_settings_tab_tests_the_stand_in_and_chooses_it_as_copilot_without_the_ids(tmp_path, madeup_out, monkeypatch):
    """While the stand-in is on, "Copilot testen" sends its one test message to the stand-in, and "Documenten lezen met" takes
    Copilot once the stand-in answered it, without the tenant and the client; Microsoft is asked nothing."""
    from aid.review.i18n import STRINGS
    from aid.tests.test_settings_tab import handler, tab
    for name, value in ON.items():
        monkeypatch.setenv(name, value)
    made_clients = []

    class Answering(Recording):
        def __init__(self, model, effort="medium"):
            super().__init__(model, effort)
            made_clients.append(self)
    monkeypatch.setattr(stand_in, "AnthropicClient", Answering)
    path, page, made = tab(tmp_path, madeup_out, test_mode=True)
    assert handler(page, "test_copilot")("", "", "nl") == STRINGS["nl"]["settings_ai_answer"].format(answer="OK")
    choice, note = handler(page, "save_reader")("llm", "nl")
    assert (choice, note) == ("llm", STRINGS["nl"]["settings_ai_switched"])
    assert json.loads(path.read_text(encoding="utf-8"))["reader"] == "llm" and made == []         # no Microsoft account made
    assert [req.user_text for client in made_clients for req in client.requests] == ["Answer with the word OK only."] * 2


def test_the_settings_tab_keeps_the_parser_when_the_stand_in_is_refused_outside_the_test_mode(tmp_path, madeup_out, monkeypatch):
    from aid.tests.test_settings_tab import handler, tab
    for name, value in ON.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(stand_in, "AnthropicClient", Recording)
    path, page, made = tab(tmp_path, madeup_out, test_mode=False)
    before = path.read_bytes()
    choice, note = handler(page, "save_reader")("llm", "en")
    assert choice == "parser" and "test mode" in note and path.read_bytes() == before
    assert "test mode" in handler(page, "test_copilot")("", "", "en")


def replayed_client(fail_first: int = 0):
    """A class in the Anthropic client's place that answers from the recorded answers of the tests, after raising a failure of the
    service (no connection) at its first `fail_first` requests; its instances list every request they got."""
    class Replayed(RecordedClient):
        failures, requests = fail_first, []

        def __init__(self, model, effort="medium"):
            super().__init__(RECORDED_DIR)
            self.name, self.model, self.effort = model, model, effort

        def complete(self, req):
            type(self).requests.append(req)
            if type(self).failures:
                type(self).failures -= 1
                raise ConnectionError("made-up outage of the service")
            return super().complete(req)
    return Replayed


def prepared_folder(tmp_path):
    """A folder of the made-up set with its purchase orders filed and an empty inbox (aid.demo --prepare-only), switched to reader
    "llm" in the test mode, as the owner switches it on the settings tab while the stand-in is on."""
    from aid.demo import main as demo
    from aid.settings import load_settings
    out = tmp_path / "live"
    assert demo(["--out", str(out), "--prepare-only"]) == 0
    path = out / "settings.json"
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")) | {"reader": "llm", "test_mode": True}), encoding="utf-8")
    return out, load_settings(path)


def results(settings, po: str) -> list:
    return [json.loads((folder / "steps" / "08_result.json").read_text(encoding="utf-8")) for folder in sorted(settings.cases_root.iterdir())
            if folder.name.startswith(f"{po}_") and (folder / "steps" / "08_result.json").exists()]


def test_a_pass_of_the_runner_reads_a_made_up_confirmation_and_a_made_up_scan_through_the_stand_in(tmp_path, monkeypatch):
    """The whole way, as in the demonstration: a made-up confirmation with a text layer (MU01) and a made-up scan (MU05) put into the
    inbox are classed by the scan, the scan by its name in the made-up set (MadeUpDecider, which asks no model), and each is read by
    the stand-in in one request with the file's own bytes."""
    import shutil
    from aid.runner import run_loop
    Replayed = replayed_client()
    monkeypatch.setattr(stand_in, "AnthropicClient", Replayed)
    out, settings = prepared_folder(tmp_path)
    shutil.copy(stand_in.MADE_UP_FOLDERS[0] / "MU01_conf.pdf", out / "inbox" / "MU01_conf.pdf")
    mail = EmailMessage()                        # a scan has no text: the order number comes from the e-mail, as from a supplier
    mail["From"], mail["To"], mail["Subject"] = "sales@supplier.example.com", "buyer@example.com", "Orderbevestiging inkooporder 250105"
    mail["Message-ID"] = "<made-up-0001@supplier.example.com>"
    mail.set_content("Bijgaand onze orderbevestiging voor inkooporder 250105.")
    mail.add_attachment((stand_in.MADE_UP_FOLDERS[0] / "MU05_conf.pdf").read_bytes(), maintype="application", subtype="pdf",
                        filename="MU05_conf.pdf")
    (out / "inbox" / "orderbevestiging_250105.eml").write_bytes(bytes(mail))
    deps = build_deps(settings, ON)
    assert isinstance(deps.decider, stand_in.MadeUpDecider)
    assert run_loop(deps, out / "STOP", poll_seconds=0, max_passes=1) == 1
    assert len(results(settings, "250101")) == 1 and len(results(settings, "250105")) == 1
    sent = {hashlib.sha256(req.pdf).hexdigest() for req in Replayed.requests}
    assert sent == {hashlib.sha256((stand_in.MADE_UP_FOLDERS[0] / name).read_bytes()).hexdigest() for name in ("MU01_conf.pdf", "MU05_conf.pdf")}


def test_a_failure_of_the_service_lets_the_case_wait_for_the_next_pass(tmp_path, monkeypatch):
    """An outage of the service is a failure of Copilot as a whole for the runner (a CopilotError): the case is left without a
    result and read at the next pass, as with Copilot, and not failed, so that the made-up document is not used up."""
    import shutil
    from aid.runner import run_loop
    Replayed = replayed_client(fail_first=1)
    monkeypatch.setattr(stand_in, "AnthropicClient", Replayed)
    out, settings = prepared_folder(tmp_path)
    shutil.copy(stand_in.MADE_UP_FOLDERS[0] / "MU01_conf.pdf", out / "inbox" / "MU01_conf.pdf")
    deps = build_deps(settings, ON)
    assert run_loop(deps, out / "STOP", poll_seconds=0, max_passes=1) == 1
    assert results(settings, "250101") == []
    assert run_loop(deps, out / "STOP", poll_seconds=0, max_passes=1) == 1
    assert len(results(settings, "250101")) == 1 and results(settings, "250101")[0]["status"] != "failed"


def test_a_document_that_is_not_made_up_is_refused_with_the_words_of_a_document_copilot_could_not_read():
    """The refusal's text begins as the runner's COULD_NOT_READ does, so that the review page shows the case as one whose document
    Copilot could not read, not as an error of the aid."""
    from aid.runner import COULD_NOT_READ
    assert stand_in.NOT_MADE_UP.startswith(COULD_NOT_READ.split("{")[0])
    with pytest.raises(ReaderError, match="^Copilot could not read this document: "):
        stand_in.StandIn(Recording("made-up-model")).complete(request(pdf=b"%PDF-1.4 made-up company document"))


@pytest.mark.parametrize("name, expected", [("MU05_conf.pdf", "confirmation"), ("MU16_conf_2.pdf", "confirmation"),
                                            ("MU05_po_v1.pdf", "po_pdf"), ("MU03_po_v2.pdf", "po_pdf"), ("ANDERS_po_v1.pdf", "po_pdf"),
                                            ("scan.pdf", None), ("MU99_conf.pdf", None), ("CART02.png", None)])
def test_the_made_up_decider_classes_a_file_of_the_set_by_its_name_and_answers_nothing_else(name, expected):
    from aid.decider import Decision
    decider = stand_in.MadeUpDecider()
    options = ["po_pdf", "confirmation", "packing_slip", "invoice", "quotation", "other"]
    answer = decider.decide(Decision(kind="document_type", options=options, context={"file_name": name}))
    assert answer.choice == expected and answer.confidence == (1.0 if expected else 0.0) and answer.source == decider.name
    assert decider.decide(Decision(kind="version", options=["1", "2"], context={"file_name": name})).choice is None
