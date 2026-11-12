"""What tells a try-out document (aid/tryout.py) apart wherever the aid has to know it, and the guards that keep a case made from one
away from a supplier, the approver and the live system (R-SAFE). From the watched folder on a try-out is an ordinary case (rule 9),
so that it proves the real chain; these guards are the only places that treat it otherwise. A try-out is a try-out confirmation, a
PDF, or a try-out cart, a page of the capture server's own try-out shop (aid/capture_server.py), and the guards hold for both.

- The scan of the watched folder (aid/sources/mailbox.py) takes a try-out only while offers_tryouts holds for the settings of the
  pass, test_mode true and no authorization limit set, as the part "Proefdraaien" of the review page offers one; otherwise it skips
  the document as TRYOUT_OUTSIDE, "try-out document outside the test mode", and moves it to processed/, so that the digest says
  why. So a try-out made before the limit was set never asks the approver. The capture server serves a try-out cart, and takes
  one that the browser extension sends, under the same condition only: otherwise it refuses the page with the same reason.
- No message about a case made from a try-out is sent or written anywhere. The pipeline (aid/pipeline.py) records each of its
  messages, the questions to a supplier and the request to the approver, as TRYOUT_NOT_SENT, "not sent (try-out document: nothing is
  sent)", without asking the mailer, and makes each finding of the message a review item with the reason NOTHING_SENT, so that the
  case comes to the buyer and does not wait for an answer that cannot come; the close of the review page (aid/review/close.py) does
  the same with its messages, and the review page refuses "Leverancier vragen" on such a case (aid/review/app.py) with "Dit is een
  proefdocument: de assistent stuurt hierover niets naar een leverancier."
- A case made from a try-out writes through the prepared writer only, never through the Exact writer or any other writer that
  reaches a system, whatever the writer of the Deps is (tryout_writer: the pipeline's run, and the close and the undo of the
  review page): its changes go to the list "In Exact invoeren" and into no system.
- The list "In Exact invoeren" gives a try-out's changes a place of their own (aid/execution/by_hand.py): they are never one change
  with a supplier's own and neither replaces the other. The tab marks them with "(proefdocument)" (aid/review/hand_list.py), in and
  out of the test mode, and the switch of the test mode says how many are still open when it turns the test mode off
  (aid/review/page.py). The digest marks the case and its rows with "(proefdocument)" and leaves its changes out of the count of the
  changes to enter in Exact (aid/notify.py, aid/runner.py).
- The count of a supplier's closed cases for "Eerste zaken van een leverancier" passes over a case made from a try-out
  (aid/pipeline.py), so that a try-out is no supplier's first case.

A try-out is known by its document alone: the first row of the first page of a try-out confirmation is FIRST_ROW,
"Proefleverancier (verzonnen document)", which no supplier prints, and the first heading of a try-out cart page is CART_HEADING,
"Proefwinkel (verzonnen pagina)", which no webshop shows; the heading is the one the cart reader reads the supplier from
(first_heading of aid/readers/cart.py). Nothing is added to its case folder and case.json is not changed, so that the cases of
the made-up batch stay as they are. tryout_case asks the case's document, the first of its input files, which is the file every
reader reads; is_tryout reads the first page of a PDF, or a page saved as HTML, once and keeps the answer by the file's path, size
and modification time. A page saved as HTML is a try-out cart only when its bytes hold CART_HEADING as the aid writes it (in UTF-8,
as the capture server saves every page, and as a browser's copy of the page keeps it) and its first heading is that text: the
first test costs one read of the file and spares the reading of every webshop's page, of which a buyer's cases hold many.

A failed read is never remembered. A read of the first page that fails, as when another program (a virus scanner, say) holds the
file for a moment, is tried again after READ_PAUSE, READ_TRIES times in all within one call, and is never kept, so that the next call
reads the file again; only a call whose every try fails answers "no try-out", for itself alone. case_is_tryout reads a case.json the
same way for the list "In Exact invoeren", the digest and the count of a supplier's closed cases. A file or a folder that is not
there is not read again: nothing holds it, and the case id of a price list, whose rows stay on the list, has no folder at all. A
case id that cannot be the name of a folder (a price list of a supplier whose name holds a slash) is no try-out's without a look
at the file system, so that such a name costs no wait at any read of the list. A file that cannot be read at all then counts as a
supplier's: judged a try-out, a real case would have its supplier's question held back and its changes marked as made up.

The one exception is the close of the review page when it is about to send a message (aid/review/close.py): there a document that
cannot be read is no supplier's either, and nothing is sent until it reads again (mark_for_sending), since the page's message goes
to the address of the order's real supplier.
"""
from functools import lru_cache
from pathlib import Path
from time import sleep

from aid.case import Case, load_case, names_a_folder
from aid.execution.writer import PreparedWriter, RetryingWriter, Writer
from aid.readers.cart import first_heading
from aid.readers.pdf_text import extract_lines
from aid.settings import Settings

READ_TRIES = 2                                  # how often one call tries to read a try-out's first page, or a case.json for it
READ_PAUSE = 0.2                                # seconds between two tries: long enough for a file held for a moment to be free
FIRST_ROW = "Proefleverancier (verzonnen document)"     # the first row of a try-out: "try-out supplier (made-up document)"
CART_HEADING = "Proefwinkel (verzonnen pagina)"         # the first heading of a try-out cart page: "try-out shop (made-up page)"
PAGES = (".html", ".htm")                               # the suffixes of a page saved as HTML, which may be a try-out cart
TRYOUT_OUTSIDE = "try-out document outside the test mode"   # why the scan skips a try-out while offers_tryouts does not hold
NOTHING_SENT = "try-out document: nothing is sent"          # the reason of a message not sent about a try-out, and of its review items
TRYOUT_NOT_SENT = f"not sent ({NOTHING_SENT})"              # the status of such a message where the messages of a case are recorded


def offers_tryouts(settings: Settings) -> bool:
    """Whether try-outs are taken: in the test mode, and while no authorization limit is set."""
    return settings.test_mode and settings.authorization_limit is None


def is_tryout_text(first_page: str) -> bool:
    """Whether the text of a first page, a line to each text line, is a try-out's: its first line that holds text is FIRST_ROW."""
    first = next((line for line in first_page.splitlines() if line.strip()), "")
    return " ".join(first.split()) == FIRST_ROW


def is_tryout(path: Path) -> bool:
    """Whether the file is a try-out: a PDF whose first page begins with FIRST_ROW, or a page saved as HTML whose bytes hold
    CART_HEADING and whose first heading is it (see the module docstring). Any other file, and one that is not there, is none. A
    file that cannot be read is tried again (_tried), and is none for this call when it still cannot be read."""
    path = Path(path)
    if path.suffix.lower() not in (".pdf", *PAGES):
        return False

    def read() -> bool:
        held = path.stat()
        return _first_row_is_tryout(str(path), held.st_size, held.st_mtime_ns)
    return _tried(read)


def tryout_case(case: Case) -> bool:
    """Whether the case is made from a try-out: its document, the first of its input files, is one."""
    return bool(case.input_files) and is_tryout(case.input_files[0])


def mark_for_sending(case: Case) -> bool | None:
    """Whether the case's document bears the try-out mark, for the one who is about to send a message about the case from the
    review page: True for a try-out, False for a document that was read at this moment and bears no mark, and None when that
    cannot be judged, because the case has no document, its file is gone or its first page cannot be read in READ_TRIES tries.
    tryout_case counts a document that cannot be read as a supplier's, so that a real case is never held back; for sending that
    is the wrong side to err on, since a message about a made-up document would reach a real supplier, so here nothing is
    guessed. A document that is no PDF can bear no mark, and is judged by its file being there."""
    if not case.input_files:
        return None
    path = Path(case.input_files[0])
    for left in reversed(range(READ_TRIES)):
        try:
            held = path.stat()
            return path.suffix.lower() == ".pdf" and _first_row_is_tryout(str(path), held.st_size, held.st_mtime_ns)
        except FileNotFoundError:
            return None
        except Exception:                        # held for a moment, being written, or no file that can be read at all
            if left:
                sleep(READ_PAUSE)
    return None


def case_is_tryout(cases_root: Path, case_id: str) -> bool:
    """tryout_case of the case <cases_root>/<case_id>, its case.json read as is_tryout reads a first page (_tried): a case whose
    case.json still cannot be read, or whose folder is gone, counts as a supplier's. A case id that cannot be the name of a folder
    (names_a_folder of aid/case.py: the case id of a price list of a supplier with a slash in its name, or with a very long name)
    is no try-out's at once: no case can have it, so nothing is read, tried again or waited for."""
    if not names_a_folder(case_id):
        return False
    return _tried(lambda: tryout_case(load_case(cases_root, case_id)))


def tryout_writer(writer: Writer, settings: Settings) -> Writer:
    """The writer a case made from a try-out writes through: the prepared writer of `writer`, itself (as every command builds its
    Deps) or the fallback of a RetryingWriter (a route into a system in front of the prepared writer, which no command builds),
    else a prepared writer into <cases_root>/prepared; never a writer that reaches a system."""
    while isinstance(writer, RetryingWriter):
        writer = writer.fallback
    return writer if isinstance(writer, PreparedWriter) else PreparedWriter(Path(settings.cases_root) / "prepared")


def _tried(read) -> bool:
    """read(), tried READ_TRIES times with READ_PAUSE between two tries while it raises; False when every try raised. A file or a
    folder that is not there (FileNotFoundError: a gone case, or the case id of a price list, which has no folder) gives False at once:
    nothing holds it, so a second try cannot find it."""
    for left in reversed(range(READ_TRIES)):
        try:
            return read()
        except FileNotFoundError:
            return False
        except Exception:                        # held for a moment, being written, or no file that can be read at all
            if left:
                sleep(READ_PAUSE)
    return False


@lru_cache(maxsize=4096)
def _first_row_is_tryout(path: str, size: int, modified: int) -> bool:
    """is_tryout for a file of this size and modification time: the size and the time are part of the key, so that a file written
    again is read again. A read that fails raises, and lru_cache keeps no exception: the cache never holds a failure."""
    if Path(path).suffix.lower() in PAGES:
        return CART_HEADING.encode("utf-8") in Path(path).read_bytes() and first_heading(Path(path)) == CART_HEADING
    lines = extract_lines(Path(path))
    return is_tryout_text("\n".join(line.text for line in lines if line.page == 1))
