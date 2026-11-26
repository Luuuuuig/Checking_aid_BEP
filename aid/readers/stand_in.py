"""A stand-in for Microsoft 365 Copilot that reads the made-up documents only: the development client over the Anthropic SDK, in
Copilot's place, for a demonstration on the owner's own PC while Copilot has no licence (the plan's amendments of 16 November).

It is on only while the process has the environment variable AID_STAND_IN set to "anthropic" (active), with ANTHROPIC_API_KEY beside it,
and only while the settings have the test mode on (stand_in_for). No setting switches it on: the settings tab cannot save it, and a
folder started as usual never has it. While it is on, it takes Copilot's place where the runner reads documents with reader "llm"
(build_deps of aid/runner.py), without the tenant and the client that Copilot needs, and where the settings tab sends Copilot its one
test message ("Copilot testen", and the choice of Copilot under "Documenten lezen met", in aid/review/page.py). The audit of a case
names the model that read it, never Copilot.

It sends nothing but the made-up documents (StandIn.complete). A request that carries a document, a PDF or a PNG image, goes out only
when each of its documents is, byte for byte, a file of the made-up set in the package (MADE_UP_FOLDERS, compared by sha256); any
other document is refused with a ReaderError before anything leaves the PC, so that a company document that reaches the inbox or
the mailbox is never sent. A document whose sha256 stands in the manifest made_up_extra.sha256 beside this module (the documents of
evaluation/unseen_cases, one sha256 per line) goes out as well; an empty or missing manifest adds none, and the sha256 of an
empty file is never accepted, even where a manifest holds it. The refusal's text begins as the runner's COULD_NOT_READ does, so that the review page shows such a case,
which fails at the pass that reads it, as one whose document Copilot could not read. A request without a document goes out only as
the one test message of the settings tab (StandIn.ask). A try-out confirmation that the settings tab makes is no file of the set
either, since it is made from a purchase order the aid knows: it is refused too. A failure of the service itself (no connection, an
overload, a limit, a key it refuses) is a CopilotError, which the runner counts as a failure of the AI service: the case is left
without a result and read again at a later pass, as with Copilot, so that the made-up document is not used up.

No model is asked for a decision (MadeUpDecider): its question holds the values of a case, which the stand-in cannot tell from a
company case's, so a case that needs a decision goes to the buyer, as with the parser. The one question it answers is the class of a
document without a text layer, which the scan can learn only from a decider: for a file named as a confirmation or a purchase order
of the made-up set (MU05_conf.pdf, MU16_conf.pdf, a name with _2 that the tab "Bestanden" gives a second copy), from its name alone,
so that the made-up scans reach the stand-in too; nothing leaves the PC for it, and a company scan so named is still refused by the
reader, by its bytes.

The test mode is read when the runner builds its reader: a runner that runs keeps the stand-in when the test mode is switched off
on the settings tab (the changed settings are then not taken, and runner.log says so) until it is started again.

The stand-in needs the library of the development client, which the folder for Windows carries since 26 November (aid/portable.py);
in a Python without it stand_in_for says so and nothing is sent.

AID_STAND_IN also takes the values "azure_openai" and "foundry" (VALUES; active is true for each of the three, and value says which):
stand_in_for then gives the exploration reader of Azure OpenAI or of Anthropic's models in Microsoft Foundry instead
(aid/readers/exploration.py), behind the same guard, with texts that name the service. Its allowed set is the one of "anthropic",
the made-up folders and the manifest, since by the owner's decision of 26 November 2026 the timed evaluation run reads the cases of
evaluation/unseen_cases through Azure OpenAI; the one-document check and the bake-off build their guard without the manifest
(exploration.guarded). It is refused, in this order, outside the test mode, which is its one switch (a mailbox in the settings is no
refusal), and without its environment variables (foundry also without the Anthropic library's client for Foundry). A count of the
tokens of a request (StandIn.count_tokens) sends the request as well, so it passes the same check first, and a failure of the
service in a count is a CopilotError as in an answer. The value "anthropic" works as before: its texts and its allowed set with the
manifest.
"""
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from aid.decider import Decision, DecisionAnswer
from aid.readers import llm_client
from aid.readers.copilot_client import CopilotError
from aid.readers.llm_client import AnthropicClient, LlmRequest, LlmResponse, ReaderError
from aid.settings import Settings

VARIABLE = "AID_STAND_IN"                        # the environment variable that switches the stand-in on, never a setting
ON = "anthropic"                                    # its one value that does
VALUES = (ON, "azure_openai", "foundry")            # the values that switch it on: the stand-in, and the exploration readers
KEY = "ANTHROPIC_API_KEY"                        # the variable the Anthropic SDK reads the key from; never written to a file
PACKAGE = Path(__file__).resolve().parent.parent
MADE_UP_FOLDERS = (PACKAGE / "madeup" / "out", PACKAGE / "madeup" / "tryout")    # the made-up set the package holds
MADE_UP_SUFFIXES = (".pdf", ".png")
EXTRA_MANIFEST = Path(__file__).resolve().parent / "made_up_extra.sha256"    # more made-up documents, by their sha256 only
_SHA256 = re.compile(r"[0-9a-f]{64}")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()    # an empty document: never sent, whatever a manifest holds
NOT_MADE_UP = ("Copilot could not read this document: the stand-in for Copilot reads the made-up documents only, and this document is "
               "none of them, so it was not sent")                      # begins as COULD_NOT_READ of aid/runner.py
NO_QUESTION = "the stand-in for Copilot sends no question but the test message of the settings tab"
NO_TEST_MODE = "the stand-in for Copilot (AID_STAND_IN=anthropic) works only in the test mode: switch the test mode on, or start without it"
NO_KEY = f"the stand-in for Copilot (AID_STAND_IN=anthropic) needs the key in the environment variable {KEY}, never in a file"
NO_LIBRARY = ("the stand-in for Copilot (AID_STAND_IN=anthropic) needs the Anthropic library, which this Python does not have: start "
              "the aid with a Python that has it, such as the folder for Windows")
TEST_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}
TEST_SYSTEM = "Answer the message in the field answer."
SERVICE_FAILED = "the stand-in for Copilot could not reach its service: {kind}"    # the class of the client's error, never its text
DOCUMENT_TYPE = "document_type"                  # the one kind of decision MadeUpDecider answers
CLASS_OF = ((re.compile(r"_conf\.pdf$"), "confirmation"), (re.compile(r"_po_v\d+\.pdf$"), "po_pdf"))    # a made-up file's name, its class
COPY = re.compile(r"_\d+(?=\.[^.]+$)")           # the _2 that the tab Bestanden puts before the suffix of a second copy


def value(environ: Mapping[str, str] | None = None) -> str:
    """The value of AID_STAND_IN in the environment (the process's own when none is given), in lower case and without the spaces
    around it; "" without the variable."""
    environ = os.environ if environ is None else environ
    return environ.get(VARIABLE, "").strip().lower()


def active(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the environment (the process's own when none is given) switches the stand-in or an exploration reader on: AID_STAND_IN
    is one of VALUES, in any case of letters and with spaces around it."""
    return value(environ) in VALUES


def extra_digests(manifest: Path = EXTRA_MANIFEST) -> frozenset[str]:
    """The sha256 of each line of the manifest that is one in lower-case hex; none for an empty, missing or unreadable manifest."""
    try:
        text = Path(manifest).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    return frozenset(line.strip() for line in text.splitlines() if _SHA256.fullmatch(line.strip())) - {EMPTY_SHA256}


def made_up_digests(folders: tuple[Path, ...] = MADE_UP_FOLDERS, manifest: Path | None = EXTRA_MANIFEST) -> frozenset[str]:
    """The sha256, in hex, of every PDF and PNG file of the made-up set and of the manifest's documents: the documents the
    stand-in may send. A manifest of None adds none, as for the exploration readers."""
    return (frozenset(hashlib.sha256(path.read_bytes()).hexdigest() for folder in folders if folder.is_dir()
                     for path in sorted(folder.iterdir()) if path.is_file() and path.suffix.lower() in MADE_UP_SUFFIXES) \
        | (frozenset() if manifest is None else extra_digests(manifest))) - {EMPTY_SHA256}


class StandIn:
    """An LlmClient over the Anthropic client that sends the made-up documents only, and, given a test text, Copilot's ask of that
    one message (see the module docstring). `name` is the client's, the model id, so that a document it read names the model. An
    exploration reader (aid/readers/exploration.py) is its client in a StandIn with its own allowed set and texts: `not_made_up`, the
    refusal of a document, and `service_failed`, the failure of the service, in which StandIn fills in {kind}."""

    def __init__(self, client, test_text: str | None = None, allowed: frozenset[str] | None = None, not_made_up: str = NOT_MADE_UP,
                 service_failed: str = SERVICE_FAILED):
        self.client, self.test_text = client, test_text
        self.allowed = made_up_digests() if allowed is None else allowed
        self.not_made_up, self.service_failed = not_made_up, service_failed
        self.name = client.name

    def complete(self, req: LlmRequest) -> LlmResponse:
        """The client's answer to a request whose every document is a file of the made-up set; a ReaderError before anything is
        sent for any other request, one without a document included."""
        return self._sent(self.client.complete, req)

    def count_tokens(self, req: LlmRequest) -> int:
        """The client's count of the input tokens of a request whose every document is a file of the made-up set. A count sends
        the request to the service as an answer does, so it is refused and its failures are told as complete's are."""
        return self._sent(self.client.count_tokens, req)

    def _sent(self, send, req: LlmRequest):
        """What `send`, the client's complete or count_tokens, gives for the request: a ReaderError (not_made_up) before anything
        is sent unless the request carries a document and each of its documents is a file of the set, and a CopilotError
        (service_failed, with the class of the error and never its text) when the service fails. The client's ReaderError stays."""
        documents = ([] if req.pdf is None else [req.pdf]) + list(req.images)
        if not documents or any(not data or hashlib.sha256(data).hexdigest() not in self.allowed for data in documents):
            raise ReaderError(self.not_made_up)
        try:
            return send(req)
        except ReaderError:
            raise
        except Exception as error:               # the service failed, not the document: the case waits for a later pass
            raise CopilotError(self.service_failed.format(kind=type(error).__name__)) from error

    def ask(self, text: str, context: list[dict] | None = None) -> LlmResponse:
        """The answer to the settings tab's one test message, in Copilot's form (the text of the answer); a ReaderError before
        anything is sent for any other message, a context, or a stand-in made without a test text."""
        if self.test_text is None or text != self.test_text or context:
            raise ReaderError(NO_QUESTION)
        response = self.client.complete(LlmRequest(system=TEST_SYSTEM, user_text=text, schema=TEST_SCHEMA, max_tokens=1000))
        try:
            answer = json.loads(response.text)["answer"]
        except (ValueError, KeyError, TypeError):
            answer = response.text
        return LlmResponse(text=str(answer), model=response.model, input_tokens=response.input_tokens,
                           output_tokens=response.output_tokens, seconds=response.seconds, cost_eur=response.cost_eur,
                           request_id=response.request_id, attempts=response.attempts)


def made_up_classes(folders: tuple[Path, ...] = MADE_UP_FOLDERS) -> dict[str, str]:
    """The class of each confirmation and purchase order of the made-up set, by its file name in lower case: "confirmation" for
    <case>_conf.pdf and "po_pdf" for <name>_po_v<n>.pdf."""
    return {path.name.lower(): kind for folder in folders if folder.is_dir() for path in sorted(folder.iterdir())
            for pattern, kind in CLASS_OF if path.is_file() and pattern.search(path.name)}


class MadeUpDecider:
    """The decider while the stand-in reads: it asks no model (see the module docstring). A document_type question about a file
    named as a confirmation or a purchase order of the made-up set gets that class, with a _<n> of a second copy taken off the name;
    every other question gets no choice, as from NoDecider."""
    name = "made-up set"

    def __init__(self, folders: tuple[Path, ...] = MADE_UP_FOLDERS):
        self.classes = made_up_classes(folders)

    def decide(self, d: Decision) -> DecisionAnswer:
        if d.kind == DOCUMENT_TYPE:
            choice = self.classes.get(COPY.sub("", str(d.context.get("file_name", ""))).lower())
            if choice in d.options:
                return DecisionAnswer(choice=choice, confidence=1.0, source=self.name,
                                      rationale="a file of the made-up set, classed by its name; no model was asked")
        return DecisionAnswer(choice=None, confidence=0.0, rationale="no decider: the stand-in for Copilot asks no model for a decision",
                              source=self.name)


def _template(text: str) -> re.Pattern[str]:
    """A pattern for a str.format template: its fixed words as they are, each {placeholder} as any text."""
    return re.compile(".+".join(re.escape(part) for part in re.split(r"\{\w+\}", text)), re.DOTALL)


def setup_kind(text: str) -> str | None:
    """What a setup refusal of the stand-in or of an exploration reader (the text of the ValueError that stand_in_for raises) asks of
    the owner: "test_mode", "missing_key" (a missing key or setting, whatever name it holds) or "missing_library"; None for any
    other text. The texts of the exploration readers are matched by their templates (aid/readers/exploration.py), so that no
    finished text is listed."""
    from aid.readers import exploration, foundry_client      # here, as in stand_in_for
    fixed = {NO_TEST_MODE: "test_mode", NO_KEY: "missing_key", NO_LIBRARY: "missing_library",
             foundry_client.NO_FOUNDRY_LIBRARY: "missing_library"}
    if (kind := fixed.get(text)) is not None:
        return kind
    for template, kind in ((exploration.NO_TEST_MODE, "test_mode"), (exploration.NO_SETTING, "missing_key")):
        if _template(template).fullmatch(text):
            return kind
    return None


def stand_in_for(settings: Settings, environ: Mapping[str, str] | None = None, test_text: str | None = None) -> StandIn:
    """The stand-in for the settings, with the model of the settings, or the package default while they hold the example's
    placeholder. Raises ValueError outside the test mode, without the key in the environment, or without the Anthropic library.
    While AID_STAND_IN is "azure_openai" or "foundry", the exploration reader of that service behind the guard instead, which also
    sends the documents of the manifest of the unseen cases (guarded with unseen_cases=True), refused with ValueError outside the
    test mode, which is its one switch, or without its environment variables (foundry also without the Anthropic library's client
    for Foundry)."""
    environ = os.environ if environ is None else environ
    chosen = value(environ)
    if chosen in VALUES and chosen != ON:
        from aid.readers import exploration      # here, so that the two modules do not import each other when they load
        if not settings.test_mode:
            raise ValueError(exploration.NO_TEST_MODE.format(service=exploration.SERVICES[chosen], value=chosen))
        return exploration.guarded(chosen, exploration.exploration_client(chosen, environ, effort=settings.effort), test_text,
                                   unseen_cases=True)
    if not settings.test_mode:
        raise ValueError(NO_TEST_MODE)
    if not environ.get(KEY):
        raise ValueError(NO_KEY)
    if llm_client.anthropic is None:
        raise ValueError(NO_LIBRARY)
    model = settings.model if not settings.model.startswith("<") else Settings.model_fields["model"].default
    return StandIn(AnthropicClient(model, settings.effort), test_text)
