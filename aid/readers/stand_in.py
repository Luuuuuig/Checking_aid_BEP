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

The library of the development client is not in the folder for Windows (aid/portable.py), so the stand-in needs a Python that has
it; without it stand_in_for says so and nothing is sent.
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
NO_LIBRARY = ("the stand-in for Copilot (AID_STAND_IN=anthropic) needs the Anthropic library, which the folder for Windows does without: "
              "start the aid with a Python that has it")
TEST_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}
TEST_SYSTEM = "Answer the message in the field answer."
SERVICE_FAILED = "the stand-in for Copilot could not reach its service: {kind}"    # the class of the client's error, never its text
DOCUMENT_TYPE = "document_type"                  # the one kind of decision MadeUpDecider answers
CLASS_OF = ((re.compile(r"_conf\.pdf$"), "confirmation"), (re.compile(r"_po_v\d+\.pdf$"), "po_pdf"))    # a made-up file's name, its class
COPY = re.compile(r"_\d+(?=\.[^.]+$)")           # the _2 that the tab Bestanden puts before the suffix of a second copy


def active(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the environment (the process's own when none is given) switches the stand-in on: AID_STAND_IN is "anthropic", in any
    case of letters and with spaces around it."""
    environ = os.environ if environ is None else environ
    return environ.get(VARIABLE, "").strip().lower() == ON


def extra_digests(manifest: Path = EXTRA_MANIFEST) -> frozenset[str]:
    """The sha256 of each line of the manifest that is one in lower-case hex; none for an empty, missing or unreadable manifest."""
    try:
        text = Path(manifest).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    return frozenset(line.strip() for line in text.splitlines() if _SHA256.fullmatch(line.strip())) - {EMPTY_SHA256}


def made_up_digests(folders: tuple[Path, ...] = MADE_UP_FOLDERS, manifest: Path = EXTRA_MANIFEST) -> frozenset[str]:
    """The sha256, in hex, of every PDF and PNG file of the made-up set and of the manifest's documents: the documents the
    stand-in may send."""
    return (frozenset(hashlib.sha256(path.read_bytes()).hexdigest() for folder in folders if folder.is_dir()
                     for path in sorted(folder.iterdir()) if path.is_file() and path.suffix.lower() in MADE_UP_SUFFIXES) \
        | extra_digests(manifest)) - {EMPTY_SHA256}


class StandIn:
    """An LlmClient over the Anthropic client that sends the made-up documents only, and, given a test text, Copilot's ask of that
    one message (see the module docstring). `name` is the client's, the model id, so that a document it read names the model."""

    def __init__(self, client, test_text: str | None = None, allowed: frozenset[str] | None = None):
        self.client, self.test_text = client, test_text
        self.allowed = made_up_digests() if allowed is None else allowed
        self.name = client.name

    def complete(self, req: LlmRequest) -> LlmResponse:
        """The client's answer to a request whose every document is a file of the made-up set; a ReaderError before anything is
        sent for any other request, one without a document included."""
        documents = ([] if req.pdf is None else [req.pdf]) + list(req.images)
        if not documents or any(not data or hashlib.sha256(data).hexdigest() not in self.allowed for data in documents):
            raise ReaderError(NOT_MADE_UP)
        try:
            return self.client.complete(req)
        except ReaderError:
            raise
        except Exception as error:               # the service failed, not the document: the case waits for a later pass
            raise CopilotError(SERVICE_FAILED.format(kind=type(error).__name__)) from error

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


def stand_in_for(settings: Settings, environ: Mapping[str, str] | None = None, test_text: str | None = None) -> StandIn:
    """The stand-in for the settings, with the model of the settings, or the package default while they hold the example's
    placeholder. Raises ValueError outside the test mode, without the key in the environment, or without the Anthropic library."""
    environ = os.environ if environ is None else environ
    if not settings.test_mode:
        raise ValueError(NO_TEST_MODE)
    if not environ.get(KEY):
        raise ValueError(NO_KEY)
    if llm_client.anthropic is None:
        raise ValueError(NO_LIBRARY)
    model = settings.model if not settings.model.startswith("<") else Settings.model_fields["model"].default
    return StandIn(AnthropicClient(model, settings.effort), test_text)
