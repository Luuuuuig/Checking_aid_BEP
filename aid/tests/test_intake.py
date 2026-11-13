"""Tests of the tab 'Bestanden' of the review page apart from the page itself (aid/review/intake.py): a file given on the page taken
into the watched folder, the lines of <cases_root>/intake.jsonl read back, and the fixed label of each outcome in both languages."""
import ast
import itertools
import json
import os
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest

from aid.readers.po_export import EXPORT_SUFFIXES, REFUSALS
from aid.review import intake
from aid.review.intake import ACCEPTED, INTAKE_FILE, NotAccepted, accept, append, recent, shown
from aid.sources import mailbox
from aid.sources.mailbox import DOCUMENT_SUFFIXES, NO_ORDER_LINES, NOT_AN_EXPORT, DocClass
from aid.tryout_mark import TRYOUT_OUTSIDE

NOW = datetime(2026, 11, 18, 10, 32, 5)
_FOLDERS = itertools.count()                       # a folder of its own for each upload of a test


def given(tmp_path: Path, name: str, data: bytes = b"%PDF-1.4 made-up") -> Path:
    """A file as the page library keeps an upload: under its own name in a folder of its own."""
    folder = tmp_path / "upload" / str(next(_FOLDERS))
    folder.mkdir(parents=True)
    path = folder / name
    path.write_bytes(data)
    return path


def test_accept_puts_the_file_in_the_watched_folder_under_a_safe_name(tmp_path):
    """The file is copied, not moved, into the watched folder (made when it is not there) under its own name made safe as the scan
    names an attachment, with _2 before its extension when the folder holds that name already; the copy is whole and leaves no file
    of its own behind (drop of aid/sources/dropin.py writes it under a dot name first)."""
    watched = tmp_path / "inbox"
    upload = given(tmp_path, "Inkooporder 250108.PDF")
    target = accept(upload, upload.name, watched)
    assert target == watched / "Inkooporder 250108.PDF" and target.read_bytes() == upload.read_bytes() and upload.exists()
    again = accept(upload, upload.name, watched)
    assert again == watched / "Inkooporder 250108_2.PDF"
    for name, safe in ((r"C:\Users\made-up\Downloads\bevestiging.pdf", "bevestiging.pdf"), ("a:b?.pdf", "a_b_.pdf"),
                       (" bijlage.eml. ", "bijlage.eml"), ("Export uit Exact.xlsx", "Export uit Exact.xlsx")):
        assert accept(given(tmp_path, "file.pdf"), name, watched, exports=True).name == safe, name
    assert sorted(path.name for path in watched.iterdir()) == ["Export uit Exact.xlsx", "Inkooporder 250108.PDF", "Inkooporder 250108_2.PDF",
                                                               "a_b_.pdf", "bevestiging.pdf", "bijlage.eml"]      # no dot file is left
    assert ACCEPTED == (".pdf", ".png", ".eml", ".xlsx", ".csv") == (*DOCUMENT_SUFFIXES, ".eml", *EXPORT_SUFFIXES)
    assert INTAKE_FILE == "intake.jsonl"


def test_accept_hands_the_file_to_drop(tmp_path, monkeypatch):
    """Rule 3 and amendment 3 (ruling O10 a): accept hands every file it does not refuse to drop of aid/sources/dropin.py, once, with
    the upload, the name as given and the watched folder, and returns what drop returns; drop writes the file under a name with a dot
    first and renames it once it is whole, so that the scan never reads half a file. A file refused is never handed to it."""
    calls = []

    def recorder(source, name, folder):
        calls.append((source, name, folder))
        return Path(folder) / "made-up.pdf"
    monkeypatch.setattr(intake, "drop", recorder)
    watched, upload = tmp_path / "inbox", given(tmp_path, "Inkooporder 250108.pdf")
    assert accept(upload, " Inkooporder 250108.pdf", watched) == watched / "made-up.pdf"
    assert calls == [(upload, " Inkooporder 250108.pdf", watched)] and not watched.exists()
    for name, data in (("notes.docx", b"made-up"), ("leeg.pdf", b""), ("Orderregels.csv", b"a;b")):
        with pytest.raises(NotAccepted):
            accept(given(tmp_path, name, data), name, watched)
    assert len(calls) == 1


def test_another_type_and_an_empty_file_are_refused(tmp_path):
    """A file of a type the scan does not read, a name without an extension among them, and an empty file are refused with
    NotAccepted, a ValueError that says why and holds the name as given; nothing is written, and the watched folder is not even made.
    The type is judged first, by the name the file would get in the watched folder. A spreadsheet is refused while the export's columns
    are not set, since the scan would skip it as a file of another type (amendment 1), and taken once they are."""
    watched = tmp_path / "inbox"
    for name, data, reason in (("notes.docx", b"made-up", "type"), ("mail.msg", b"made-up", "type"), ("scan", b"%PDF made-up", "type"),
                               ("lijst.xls", b"made-up", "type"), ("leeg.docx", b"", "type"), ("leeg.pdf", b"", "empty"),
                               ("Orderregels.xlsx", b"PK made-up", "export"), ("Orderregels.csv", b"a;b", "export"),
                               ("leeg.csv", b"", "export")):
        with pytest.raises(NotAccepted) as refused:
            accept(given(tmp_path, name, data), name, watched)
        assert (refused.value.name, refused.value.reason) == (name, reason), name
        assert isinstance(refused.value, ValueError) and name not in str(refused.value), name
    assert not watched.exists()
    with pytest.raises(NotAccepted) as refused:
        accept(given(tmp_path, "leeg.csv", b""), "leeg.csv", watched, exports=True)
    assert refused.value.reason == "empty" and not watched.exists()
    assert accept(given(tmp_path, "Orderregels.csv", b"a;b"), "Orderregels.csv", watched, exports=True) == watched / "Orderregels.csv"
    assert intake.REASONS == ("type", "export", "empty")


def test_recent_gives_the_newest_first_and_passes_a_damaged_line_over(tmp_path):
    """append writes a JSON line for each event at the time given; recent gives the last lines, the newest first, at most `limit`,
    passing over a line that cannot be read: no JSON, no object, a field missing or not text, a time that is no time, an outcome the
    table has no label for, a detail that has not the form of its outcome (ruling O10 c), and the last line while it is being
    written."""
    root = tmp_path / "cases"
    assert recent(root) == []                                                  # no file yet: nothing handled
    append(root, [("PO.eml/Inkooporder.pdf", "po", "250108 v1"), ("bevestiging.pdf", "case", "250108_20261118T103205")], NOW)
    path = root / INTAKE_FILE
    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == [
        {"time": "2026-11-18T10:32:05", "file": "PO.eml/Inkooporder.pdf", "outcome": "po", "detail": "250108 v1"},
        {"time": "2026-11-18T10:32:05", "file": "bevestiging.pdf", "outcome": "case", "detail": "250108_20261118T103205"}]
    with open(path, "a", encoding="utf-8") as f:
        for damaged in ("{\"time\": \"2026-11-18T10:33:00\", \"file\": \"x.pdf\", \"outc", "[1, 2]", "not json",
                        json.dumps({"time": "2026-11-18T10:33:00", "file": "x.pdf", "outcome": "po"}),
                        json.dumps({"time": "2026-11-18T10:33:00", "file": 5, "outcome": "po", "detail": "1 v1"}),
                        json.dumps({"time": "gisteren", "file": "x.pdf", "outcome": "po", "detail": "1 v1"}),
                        json.dumps({"time": "2026-11-18T10:33:00", "file": "x.pdf", "outcome": "printed", "detail": ""}),
                        *(json.dumps({"time": "2026-11-18T10:33:00", "file": "x.pdf", "outcome": outcome, "detail": detail})
                          for outcome, detail in (("po", "250108"), ("po", "250108 vx"), ("export", "3"), ("export", "3 x"),
                                                  ("case", "250108"), ("guess", "250108_gisteren"), ("answer", "x"),
                                                  ("recheck", "a/b_20261118T103205"), ("bounce", ""), ("auto", "250117"),
                                                  ("unread", ""), ("unread", "250116"), ("own", "250117"),
                                                  ("set_aside", "dertig"), ("set_aside", "-3")))):
            f.write(damaged + "\n")
    append(root, [("notes.docx", "skipped", "unsupported file type: .docx")], datetime(2026, 11, 18, 10, 34))
    with open(path, "a", encoding="utf-8") as f:
        f.write("{\"time\": \"2026-11-18T10:35:00\", \"file\": \"cut")            # the runner is writing it
    assert [(line["file"], line["outcome"]) for line in recent(root)] == [
        ("notes.docx", "skipped"), ("bevestiging.pdf", "case"), ("PO.eml/Inkooporder.pdf", "po")]
    assert [line["file"] for line in recent(root, limit=2)] == ["notes.docx", "bevestiging.pdf"]
    append(root, [("na.pdf", "error", "")], datetime(2026, 11, 18, 10, 36))       # a line after the cut one starts a line of its own
    assert [line["file"] for line in recent(root, limit=2)] == ["na.pdf", "notes.docx"]
    many = tmp_path / "many"                                                     # a long file is read from its end
    append(many, [(f"f{n:05}.pdf", "skipped", "duplicate") for n in range(5000)], NOW)
    assert [line["file"] for line in recent(many, limit=3)] == ["f04999.pdf", "f04998.pdf", "f04997.pdf"]
    assert len(recent(many)) == 20 and len(recent(many, limit=5000)) == 5000


def test_the_time_is_shown_as_the_page_shows_a_time(tmp_path):
    """The table shows the time of a line as "18-11-2026 10:32", the day first as the page shows a day elsewhere (B4.5 of the
    redesign), in both languages: a time without a zone as it is, the runner's clock being the PC's local time, and a time with a
    zone in the local time of this PC."""
    assert intake.when({"time": "2026-11-18T10:32:05"}) == "18-11-2026 10:32"
    aware = datetime(2026, 11, 18, 9, 32, 5).astimezone()
    assert intake.when({"time": aware.isoformat()}) == "18-11-2026 09:32"
    root = tmp_path / "cases"
    append(root, [("bevestiging.pdf", "case", "250108_20261118T103205")], NOW)
    assert intake.rows(root, "nl") == [["18-11-2026 10:32", "bevestiging.pdf", "Zaak geopend voor inkooporder 250108"]]
    assert intake.rows(root, "en") == [["18-11-2026 10:32", "bevestiging.pdf", "Case opened for purchase order 250108"]]


LABELS = [  # (outcome, detail, Dutch, English): every outcome, and every reason of a skip the scan gives
    ("po", "250108 v1", "Inkooporder 250108 opgenomen (versie 1)", "Purchase order 250108 filed (version 1)"),
    ("po", "IB 2600417 v12", "Inkooporder IB 2600417 opgenomen (versie 12)", "Purchase order IB 2600417 filed (version 12)"),
    ("export", "2 1", "Export uit Exact opgenomen: 2 inkooporders, waarvan 1 nieuw of gewijzigd",
     "Export from Exact filed: 2 purchase orders, of which 1 new or changed"),
    ("export", "1 0", "Export uit Exact opgenomen: 1 inkooporder, waarvan 0 nieuw of gewijzigd",
     "Export from Exact filed: 1 purchase order, of which 0 new or changed"),
    ("case", "250108_20261118T103205", "Zaak geopend voor inkooporder 250108", "Case opened for purchase order 250108"),
    ("case", "unknown_20261118T103205", "Zaak geopend, inkooporder onbekend", "Case opened, purchase order unknown"),
    ("guess", "250108_20261118T103205", "Zaak geopend voor inkooporder 250108, afgeleid: kijk de zaak na",
     "Case opened for purchase order 250108, a guess: check the case"),
    ("answer", "250117_20261116T080000", "Antwoord van een leverancier bij de zaak gelegd", "A supplier's answer filed with its case"),
    ("recheck", "250101_20261112T090200", "Inkooporder 250101 is binnen: de wachtende zaak is opnieuw gecontroleerd",
     "Purchase order 250101 is in: the waiting case was checked again"),
    ("skipped", "duplicate", "Al eerder ontvangen: niets gedaan", "Received before: nothing done"),
    ("skipped", "invoice", "Geen inkooporder of orderbevestiging: niets gedaan", "No purchase order or order confirmation: nothing done"),
    ("skipped", "unsupported file type: .docx", "Dit soort bestand leest de assistent niet", "The assistant does not read this kind of file"),
    ("skipped", "unsupported file type: no extension", "Dit soort bestand leest de assistent niet",
     "The assistant does not read this kind of file"),
    ("skipped", "more than one PO number", "Meer dan een inkoopordernummer gevonden: bekijk dit document zelf",
     "More than one purchase order number found: look at this document yourself"),
    ("skipped", "no text layer; class unknown", "Scan zonder leesbare tekst: bekijk dit document zelf",
     "A scan without readable text: look at this document yourself"),
    ("skipped", "no attachment", "E-mail zonder bijlage: niets gedaan", "An e-mail without an attachment: nothing done"),
    ("skipped", NOT_AN_EXPORT, "Een rekenblad zonder de kolomtitels van de export: niets gedaan",
     "A spreadsheet without the export's column titles: nothing done"),
    ("skipped", TRYOUT_OUTSIDE, "Proefdocument buiten de proefstand: niets gedaan", "A try-out document outside the test mode: nothing done"),
    ("error", "", "Kon niet worden gelezen. Is het een inkooporder uit Exact, controleer dan de opmaak onder Instellingen, Koppeling met Exact.",
     "Could not be read. If it is a purchase order from Exact, check the layout under Settings, Connection to Exact."),
    ("error", "export: no_po_number", "Export uit Exact niet opgenomen: een rij heeft een artikelcode maar geen inkoopordernummer",
     "Export from Exact not filed: a row has an item code but no purchase order number"),
    ("error", "export: no_column", "Export uit Exact niet opgenomen: de kolommen van dit bestand zijn anders dan ingesteld",
     "Export from Exact not filed: the columns of this file differ from those set"),
    ("error", "export: po_number_date", "Export uit Exact niet opgenomen: controleer de kolommen onder Instellingen, Koppeling met Exact.",
     "Export from Exact not filed: check the columns under Settings, Connection to Exact."),
    # the review round (rulings O5, O6, O8 and O11)
    ("bounce", "250117_20261116T080000", "Een bericht van de assistent kwam terug als onbestelbaar: bij de zaak gelegd",
     "A message of the assistant came back as undeliverable: filed with its case"),
    ("auto", "250117_20261116T080000", "Automatisch antwoord (bijvoorbeeld een afwezigheidsmelding) bij de zaak gelegd",
     "An automatic reply (an out-of-office notice, for example) filed with its case"),
    ("error", "version", "Inkooporder niet opgenomen: de assistent heeft deze versie al uit een ander bestand",
     "Purchase order not filed: the assistant holds this version already from another file"),
    ("set_aside", "31", "Na 31 dagen nog niet gelezen: de assistent probeert dit bestand niet meer",
     "Not read after 31 days: the assistant no longer tries this file"),
    ("skipped", NO_ORDER_LINES, "Export uit Exact zonder orderregels: niets gedaan", "An export from Exact without order lines: nothing done"),
    # a case opened from a scan that failed at reading (Task 44)
    ("unread", "250116_20261118T103205", "Scan zonder leesbare tekst: de assistent kon dit document niet lezen; bekijk het zelf",
     "A scan without readable text: the assistant could not read this document; look at it yourself"),
    # the buyer's own message that names a question (the review round of Task 44)
    ("own", "250117_20261116T080000", "Uw eigen bericht bij de zaak gelegd; dit telt niet als antwoord van de leverancier",
     "Your own message filed with the case; this does not count as the supplier's answer"),
]


@pytest.mark.parametrize("outcome, detail, dutch, english", LABELS)
def test_each_outcome_has_its_label_in_both_languages(outcome, detail, dutch, english):
    event = {"time": "2026-11-18T10:32:05", "file": "x.pdf", "outcome": outcome, "detail": detail}
    assert (shown(event, "nl"), shown(event, "en")) == (dutch, english)


def test_every_outcome_and_every_class_has_a_label():
    """Every outcome of the intake file has its label, and so has every class the scan skips a document by (all but a PO and a
    confirmation, which it files): a document of another class is no purchase order or confirmation."""
    assert set(intake.OUTCOMES) == {outcome for outcome, *_ in LABELS}
    for doc_class in set(get_args(DocClass)) - {"po_pdf", "confirmation"}:
        assert shown({"outcome": "skipped", "detail": doc_class}, "nl") == "Geen inkooporder of orderbevestiging: niets gedaan", doc_class


def skip_reasons() -> list[ast.expr]:
    """The reason given to each call of skip in aid/sources/mailbox.py, as the source writes it."""
    tree = ast.parse(Path(mailbox.__file__).read_text(encoding="utf-8"))
    return [node.args[2] for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "skip"]


def test_every_reason_the_scan_skips_a_document_for_has_its_own_label():
    """Amendment 9: every reason of a skip that the scan can give now (the calls of skip in aid/sources/mailbox.py, read from the source
    so that a reason added later without a label fails here) has a label of its own, never the label of a reason the table does not
    know ("Overgeslagen: niets gedaan"), which is the label of no reason the scan gives today."""
    unknown = shown({"outcome": "skipped", "detail": "a reason of a later version"}, "nl")
    assert unknown == "Overgeslagen: niets gedaan" and shown({"outcome": "skipped", "detail": "x"}, "en") == "Skipped: nothing done"
    reasons = []
    for node in skip_reasons():
        if isinstance(node, ast.Constant):
            reasons.append(node.value)
        elif isinstance(node, ast.Name):
            reasons.append(getattr(mailbox, node.id))
        elif isinstance(node, ast.JoinedStr):                         # "unsupported file type: {the extension}"
            reasons.append("".join(part.value if isinstance(part, ast.Constant) else ".docx" for part in node.values))
        elif isinstance(node, ast.BoolOp):                            # document.kind or "other": a class the scan does not file
            reasons.extend(set(get_args(DocClass)) - {"po_pdf", "confirmation"})
        else:
            raise AssertionError(f"a reason of a form this test does not know: {ast.dump(node)}")
    assert {reason.split(":")[0] for reason in reasons} >= {"duplicate", "no attachment", "unsupported file type", NOT_AN_EXPORT,
                                                            TRYOUT_OUTSIDE, "no text layer; class unknown", "more than one PO number",
                                                            NO_ORDER_LINES}
    for reason in reasons:
        for language in ("nl", "en"):
            assert shown({"outcome": "skipped", "detail": reason}, language) != shown({"outcome": "skipped", "detail": "?"}, language), reason


def test_every_reason_the_export_reader_refuses_a_file_for_has_its_label():
    """Amendment 1: a refused export shows the label of its reason (ExportRefused.reason, one of REFUSALS of the export reader), from one
    table: a row with an item code and no PO number, the columns of the file (a column of the map that no row holds, a title that
    stands twice), a row with another number of cells than the row of titles, a workbook with more than one sheet with data or with
    data on a hidden sheet only (ruling Z31 of the price list's third review round: each a line of its own, since the columns are
    right then), and for every other reason the line that sends the buyer to the columns on the settings tab. An export refused for
    a reason the table does not know shows that line too."""
    assert set(intake.EXPORT_REFUSED) == set(REFUSALS)
    nl = {"no_po_number": "Export uit Exact niet opgenomen: een rij heeft een artikelcode maar geen inkoopordernummer",
          "columns": "Export uit Exact niet opgenomen: de kolommen van dit bestand zijn anders dan ingesteld",
          "cells": ("Export uit Exact niet opgenomen: een rij heeft een ander aantal cellen dan de rij met kolomtitels. Sla het bestand op als "
                    "Excel-bestand (.xlsx) en geef het opnieuw."),
          "sheets": ("Export uit Exact niet opgenomen: het Excel-bestand heeft meer dan één werkblad met gegevens. Sla het werkblad met de "
                     "orderregels op als een eigen bestand en geef dat."),
          "hidden_sheet": ("Export uit Exact niet opgenomen: het Excel-bestand heeft alleen gegevens op een verborgen werkblad. Maak het "
                           "werkblad zichtbaar en geef het bestand opnieuw."),
          "check": "Export uit Exact niet opgenomen: controleer de kolommen onder Instellingen, Koppeling met Exact."}
    own = {"no_po_number": "no_po_number", "no_column": "columns", "double_column": "columns", "surplus_cells": "cells", "cell_count": "cells",
           "several_sheets": "sheets", "hidden_sheet": "hidden_sheet"}
    assert set(own) <= set(REFUSALS)
    for reason in REFUSALS:
        expected = nl[own.get(reason, "check")]
        assert shown({"outcome": "error", "detail": f"export: {reason}"}, "nl") == expected, reason
        assert shown({"outcome": "error", "detail": f"export: {reason}"}, "en").startswith("Export from Exact not filed: "), reason
    assert shown({"outcome": "error", "detail": "export: cell_count"}, "en") == (
        "Export from Exact not filed: a row has another number of cells than the row of column titles. Save the file as an Excel file "
        "(.xlsx) and give it again.")
    assert shown({"outcome": "error", "detail": "export: several_sheets"}, "en") == (
        "Export from Exact not filed: the Excel file has more than one sheet with data. Save the sheet with the order lines as a file of "
        "its own and give that.")
    assert shown({"outcome": "error", "detail": "export: hidden_sheet"}, "en") == (
        "Export from Exact not filed: the Excel file holds data on a hidden sheet only. Unhide the sheet and give the file again.")
    assert shown({"outcome": "error", "detail": "export: a reason of a later version"}, "nl") == nl["check"]


def error_tuples() -> list[ast.Tuple]:
    """Each tuple of aid/sources/mailbox.py whose third element is the outcome "error": the events of an error, as the source writes
    them."""
    tree = ast.parse(Path(mailbox.__file__).read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Tuple) and len(node.elts) == 4
            and isinstance(node.elts[2], ast.Constant) and node.elts[2].value == "error"]


def test_every_detail_of_an_error_has_its_label_in_one_table():
    """Ruling O6 (finding 11): every form of the detail of an error stands in one table with its label (intake.ERRORS), held against
    the scan: the detail the scan gives each kind of error it tells apart (error_detail of aid/sources/mailbox.py: an export refused for
    each reason of REFUSALS, the store's refusal of a version it holds from another file, any other error, whatever its text) is a key
    of the table and every key comes of one; every event of an error in mailbox.py's source takes its detail from error_detail. The
    store's refusal is told apart by its class (VersionHeld), never by its text."""
    from aid.po_store import VersionHeld
    from aid.readers.po_export import ExportRefused
    from aid.review.i18n import STRINGS
    made = {mailbox.error_detail(ExportRefused("made-up", reason)) for reason in REFUSALS}
    made |= {mailbox.error_detail(error) for error in (VersionHeld("made-up"), ValueError("is version 1 of PO 250101, which is stored from"
                                                                                           " another file"), OSError(13, "made-up"),
                                                       RuntimeError("made-up"))}
    assert made == set(intake.ERRORS) and mailbox.error_detail(VersionHeld("made-up")) == "version"
    for detail, key in intake.ERRORS.items():
        for language in ("nl", "en"):
            assert shown({"outcome": "error", "detail": detail}, language) == STRINGS[language][key], detail
    tuples = error_tuples()
    assert tuples and all(isinstance(node.elts[3], ast.Call) and getattr(node.elts[3].func, "id", None) == "error_detail" for node in tuples)


def test_the_table_never_shows_the_text_of_an_error():
    """Rule 4: an error is shown by its fixed label, whatever its detail holds, and a detail is never shown as it is."""
    general = shown({"outcome": "error", "detail": ""}, "nl")
    for detail in ("error: MU01_po_v1.pdf: no PO number found", "Traceback (most recent call last)", "PdfminerException: made-up"):
        assert shown({"outcome": "error", "detail": detail}, "nl") == general and detail not in general


def test_the_texts_of_the_tab_in_both_languages():
    """The tab's own texts, word for word (the brief and amendment 1, the note of a file accepted as the redesign of the page words
    it), and the two that the brief does not give: the note of a press without a file, and the label of a reason of a skip the
    table does not know."""
    from aid.review.i18n import STRINGS
    nl, en = STRINGS["nl"], STRINGS["en"]
    texts = {"files_tab": ("Bestanden", "Files"), "files_title": ("Een bestand aan de assistent geven", "Give a file to the assistant"),
             "files_intro": ("Komt een document niet vanzelf uit uw postvak, geef het dan hier: een inkooporder uit Exact (pdf) of een "
                             "orderbevestiging (pdf of e-mail). De assistent verwerkt het bij de volgende ronde, binnen een minuut.",
                             "If a document does not come from your mailbox by itself, give it here: a purchase order from Exact (PDF) "
                             "or an order confirmation (PDF or e-mail). The assistant handles it at its next pass, within a minute."),
             "files_intro_export": ("Komt een document niet vanzelf uit uw postvak, geef het dan hier: een inkooporder uit Exact (pdf), een "
                                    "orderbevestiging (pdf of e-mail) of een export uit Exact (Excel of csv). De assistent verwerkt het bij "
                                    "de volgende ronde, binnen een minuut.",
                                    "If a document does not come from your mailbox by itself, give it here: a purchase order from Exact "
                                    "(PDF), an order confirmation (PDF or e-mail) or an export from Exact (Excel or CSV). The assistant "
                                    "handles it at its next pass, within a minute."),
             "files_choose": ("Kies een bestand", "Choose a file"), "files_give": ("Aan de assistent geven", "Give to the assistant"),
             "files_accepted": ("{name} is aangenomen. De assistent leest het bij de volgende ronde, binnen een minuut; wat ermee "
                                "gebeurt, komt dan vanzelf in Laatst verwerkt.",
                                "{name} was accepted. The assistant reads it at its next pass, within a minute; what happens to it then "
                                "appears under Handled last by itself."),
             "files_refused:type": ("{name} is niet aangenomen: de assistent leest pdf, png en e-mail (.eml).",
                                    "{name} was not accepted: the assistant reads PDF, PNG and e-mail (.eml)."),
             "files_refused:type_export": ("{name} is niet aangenomen: de assistent leest pdf, png, e-mail (.eml) en een export uit Exact "
                                           "(Excel of csv).",
                                           "{name} was not accepted: the assistant reads PDF, PNG, e-mail (.eml) and an export from Exact "
                                           "(Excel or CSV)."),
             "files_refused:empty": ("{name} is niet aangenomen: het bestand is leeg.", "{name} was not accepted: the file is empty."),
             "files_refused:export": ("{name} is niet aangenomen: de assistent leest een export uit Exact pas als de kolommen zijn "
                                      "ingesteld onder Instellingen, Koppeling met Exact.",
                                      "{name} was not accepted: the assistant reads an export from Exact only once its columns are set "
                                      "under Settings, Connection to Exact."),
             "files_choose_first": ("Kies eerst een bestand.", "Choose a file first."),
             "files_done": ("Laatst verwerkt", "Handled last"), "files_column:time": ("Tijd", "Time"), "files_column:file": ("Bestand", "File"),
             "files_column:what": ("Wat de assistent deed", "What the assistant did"), "refresh": ("Lijst vernieuwen", "Refresh the list")}
    for key, (dutch, english) in texts.items():
        assert (nl[key], en[key]) == (dutch, english), key
    for strings in (nl, en):
        own = "".join(value for key, value in strings.items() if key.startswith("files"))
        assert "\u2014" not in own and "\u2013" not in own                       # no dashes of that kind in the buyer's texts


def test_the_intake_file_holds_names_numbers_and_case_ids_and_stays_under_the_cases_root(tmp_path):
    """Rule 6: append writes the file under the cases root and a line holds the time, the file, the outcome and the detail and nothing
    else."""
    root = tmp_path / "cases"
    append(root, [("m.eml/MU01_conf.pdf", "case", "250101_20261118T103205")], NOW)
    assert [path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()] == ["cases/intake.jsonl"]
    [line] = (root / INTAKE_FILE).read_text(encoding="utf-8").splitlines()
    assert set(json.loads(line)) == {"time", "file", "outcome", "detail"} and os.linesep not in line


def test_a_line_that_opened_a_case_says_what_the_case_is_now(tmp_path, madeup_out):
    """Findings 3 and 4: behind the label of a line that opened a case (the outcomes case, guess and recheck) the table says what the
    case is now, read from the case's folder when the table is read, "Zaak geopend voor inkooporder 250105. Nu: mislukt.", with the
    mark of a try-out before it. A case in review keeps the label alone, one that waits for its purchase order too, and so does a
    case whose folder is gone. No other outcome changes, also one that names a case, and the intake file is not written."""
    from aid.case import open_case
    from aid.review.i18n import STRINGS
    from aid.tests.conftest import deps
    from aid.tests.fakes import case_result
    from aid.tests.test_status import UNTEMPLATED, WAITS, digest, folder, handled, records
    from aid.tryout import confirmation_pdf, made_from
    root = tmp_path / "cases"
    (tmp_path / "document.pdf").write_bytes((madeup_out / "MU01_conf.pdf").read_bytes())    # the document of each case: a supplier's
    made = {state: folder(root, number, datetime(2026, 11, 18, 9, minute), saved, **update).name for minute, (state, number, saved, update) in enumerate((
        ("failed", "250105", "failed", {"error": UNTEMPLATED}), ("handled", "250106", "failed", {"error": UNTEMPLATED}),
        ("closed_itself", "250102", "closed", {}), ("closed_by_you", "250107", "closed", {}), ("waiting", "250101", "waiting", {}),
        ("copilot_wait", "250112", None, {}), ("reading", "250109", None, {}), ("review", "250110", "review", {}),
        ("waiting_po", "250111", "review", {"version": WAITS})))}
    handled(root / made["handled"])
    records(root / made["closed_by_you"], "close")
    digest(root, copilot_cases={made["copilot_wait"]: {"refused": 1, "failed": 0, "read_after": False}})
    tryout_pdf = tmp_path / "PROEF orderbevestiging 250108.pdf"
    tryout_pdf.write_bytes(confirmation_pdf(made_from(deps(tmp_path, madeup_out).store, "250108"), "price_small", datetime(2026, 11, 18, 8, 59)))
    tryout = open_case(root, "confirmation", [tryout_pdf], "250108", datetime(2026, 11, 18, 9, 30))
    tryout.write_step(8, "result", case_result(tryout.case_id, "closed", supplier="S08"))
    events = [*((f"{state}.pdf", "case", case_id) for state, case_id in made.items()),
              ("gissing.pdf", "guess", made["failed"]), ("opnieuw.pdf", "recheck", made["closed_itself"]),
              ("PROEF orderbevestiging 250108.pdf", "case", tryout.case_id), ("weg.pdf", "case", "250199_20261118T090000"),
              ("antwoord.eml", "answer", made["closed_by_you"]), ("scan.pdf", "unread", made["failed"]),
              ("PO.eml/Inkooporder.pdf", "po", "250105 v1"), ("dubbel.pdf", "skipped", "duplicate")]
    append(root, events, NOW)
    written = (root / INTAKE_FILE).read_bytes()
    nl = {row[1]: row[2] for row in intake.rows(root, "nl", limit=50)}
    assert nl == {
        "failed.pdf": "Zaak geopend voor inkooporder 250105. Nu: mislukt.",
        "handled.pdf": "Zaak geopend voor inkooporder 250106. Nu: mislukt, door u afgehandeld.",
        "closed_itself.pdf": "Zaak geopend voor inkooporder 250102. Nu: vanzelf afgesloten.",
        "closed_by_you.pdf": "Zaak geopend voor inkooporder 250107. Nu: door u afgesloten.",
        "waiting.pdf": "Zaak geopend voor inkooporder 250101. Nu: wacht op de leverancier.",
        "copilot_wait.pdf": "Zaak geopend voor inkooporder 250112. Nu: wacht op Copilot.",
        "reading.pdf": "Zaak geopend voor inkooporder 250109. Nu: wordt gelezen.",
        "review.pdf": "Zaak geopend voor inkooporder 250110",
        "waiting_po.pdf": "Zaak geopend voor inkooporder 250111",
        "gissing.pdf": "Zaak geopend voor inkooporder 250105, afgeleid: kijk de zaak na. Nu: mislukt.",
        "opnieuw.pdf": "Inkooporder 250102 is binnen: de wachtende zaak is opnieuw gecontroleerd. Nu: vanzelf afgesloten.",
        "PROEF orderbevestiging 250108.pdf": "Zaak geopend voor inkooporder 250108 (proefdocument). Nu: vanzelf afgesloten.",
        "weg.pdf": "Zaak geopend voor inkooporder 250199",
        "antwoord.eml": "Antwoord van een leverancier bij de zaak gelegd",
        "scan.pdf": "Scan zonder leesbare tekst: de assistent kon dit document niet lezen; bekijk het zelf",
        "PO.eml/Inkooporder.pdf": "Inkooporder 250105 opgenomen (versie 1)",
        "dubbel.pdf": "Al eerder ontvangen: niets gedaan"}
    en = {row[1]: row[2] for row in intake.rows(root, "en", limit=50)}
    assert (en["failed.pdf"], en["handled.pdf"], en["PROEF orderbevestiging 250108.pdf"], en["review.pdf"]) == (
        "Case opened for purchase order 250105. Now: failed.", "Case opened for purchase order 250106. Now: failed, handled by you.",
        "Case opened for purchase order 250108 (try-out document). Now: closed by itself.", "Case opened for purchase order 250110")
    for language in ("nl", "en"):
        rows = {row[1]: row[2] for row in intake.rows(root, language, limit=50)}
        for event in events:
            if event[1] not in ("case", "guess", "recheck") or event[0] in ("review.pdf", "waiting_po.pdf", "weg.pdf"):
                assert rows[event[0]] == shown({"outcome": event[1], "detail": event[2]}, language), event
    assert {key: STRINGS["nl"][key] for key in STRINGS["nl"] if key.startswith("files_now")} == {
        "files_now": "{label}. Nu: {state}.", "files_now:failed": "mislukt", "files_now:handled": "mislukt, door u afgehandeld",
        "files_now:waiting": "wacht op de leverancier", "files_now:closed_itself": "vanzelf afgesloten",
        "files_now:closed_by_you": "door u afgesloten", "files_now:copilot_wait": "wacht op Copilot", "files_now:reading": "wordt gelezen"}
    assert STRINGS["en"]["files_now"] == "{label}. Now: {state}." and (root / INTAKE_FILE).read_bytes() == written
