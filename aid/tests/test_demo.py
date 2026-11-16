"""The one-command demo of aid/demo.py: a folder with valid settings, the 22 made-up cases and the batch's first pass."""
import csv
import hashlib

from aid import demo
from aid.demo import RECORDED_DIR, main
from aid.readers.llm_client import RecordedClient
from aid.runner import run_loop
from aid.runner import build_deps
from aid.review.app import list_open_cases
from aid.settings import load_settings
from aid.tests.conftest import read_csv
from aid.tests.test_batch import PARSER_STATUS, RECORDED_STATUS


def test_the_demo_prepares_the_folder_and_runs_the_made_up_set(tmp_path, capsys):
    """One command gives a settings file that loads, the 22 cases, the summary with the statuses the batch pins and the folders
    the review page and the runner need."""
    out = tmp_path / "demo"
    assert main(["--out", str(out)]) == 0
    settings = load_settings(out / "settings.json")
    assert settings.reader == "parser" and settings.cases_root == out / "cases" and settings.watched_folder == out / "inbox"
    assert settings.supplier_lead_times["S01"] == 10 and settings.supplier_lead_times["S22"] == 10
    assert settings.send_via == "auto"               # the batch's own settings stay as they were: its files are compared with earlier runs
    cases = read_csv(out / "cases.csv")
    assert [row["case_id"] for row in cases] == list(PARSER_STATUS) and {row["kind"] for row in cases} == {"confirmation", "cart"}
    assert cases[0]["po_number"] == "250101" and cases[-1]["document"].endswith("CART02.html")
    summary = read_csv(out / "summary.csv")
    expected = PARSER_STATUS | {"CART02": "review"}                  # the demo gives CART02 as its saved page, which the parser run reads
    assert {row["case_id"]: row["status"] for row in summary} == expected
    assert (out / "inbox").is_dir() and (out / "cases" / "prepared" / "prepared_changes.csv").exists()
    open_pos = {summary.po_number for summary in list_open_cases(settings.cases_root)}       # the page lists the cases in review
    assert open_pos == {row["po_number"] for row in cases if expected[row["case_id"]] == "review"}
    text = capsys.readouterr().out
    assert "22 made-up cases run with the parser reader" in text and "MU05, MU16" in text and "aid.review" in text


def test_the_recorded_reader_replays_every_document(tmp_path):
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--reader", "recorded"]) == 0
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == RECORDED_STATUS


def test_a_folder_that_is_not_empty_is_refused_without_force(tmp_path, capsys):
    out = tmp_path / "demo"
    out.mkdir()
    (out / "note.txt").write_text("x", encoding="utf-8")
    assert main(["--out", str(out)]) == 2 and "not empty" in capsys.readouterr().err
    assert not (out / "settings.json").exists()
    assert main(["--out", str(out), "--force"]) == 0 and (out / "summary.csv").exists()


def test_the_anthropic_reader_needs_the_key_in_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["--out", str(tmp_path / "demo"), "--reader", "anthropic"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err and not (tmp_path / "demo" / "summary.csv").exists()


def test_the_anthropic_reader_is_wired_like_the_notebook(tmp_path, monkeypatch):
    """With the client replaced by the recorded one (no call), the live option reads every document and asks the decider."""
    class Replayed(RecordedClient):
        def __init__(self, model, effort="medium"):
            super().__init__(RECORDED_DIR)
            self.model, self.effort = model, effort
    monkeypatch.setenv("ANTHROPIC_API_KEY", "made-up-key-for-the-test")
    monkeypatch.setattr(demo, "AnthropicClient", Replayed)
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--reader", "anthropic"]) == 0
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == RECORDED_STATUS


def test_a_case_named_runs_alone_and_force_adds_another_to_the_folder(tmp_path, capsys):
    """--case runs only the cases named, in any case of letters, so that a demonstration sends one document at a time; --force adds
    the next case to the folder, whose review page then holds both."""
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--case", "MU01"]) == 0
    assert [row["case_id"] for row in read_csv(out / "cases.csv")] == ["MU01"]
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == {"MU01": PARSER_STATUS["MU01"]}
    assert "1 made-up case run with the parser reader" in capsys.readouterr().out
    assert main(["--out", str(out), "--case", "mu07"]) == 2 and "not empty" in capsys.readouterr().err
    assert main(["--out", str(out), "--case", "mu07", "--force"]) == 0
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == {"MU07": PARSER_STATUS["MU07"]}
    folders = {path.name.split("_")[0] for path in (out / "cases").iterdir() if path.is_dir() and path.name[:6].isdigit()}
    assert {"250101", "250107"} <= folders


def test_a_case_that_is_not_in_the_made_up_set_is_refused_before_anything_is_written(tmp_path, capsys):
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--case", "MU05", "--case", "MU99"]) == 2
    err = capsys.readouterr().err
    assert "MU99" in err and "MU01" in err and "CART02" in err and not out.exists()
    assert main(["--out", str(out), "--case", "MU05", "--prepare-only"]) == 2
    assert "--prepare-only" in capsys.readouterr().err and not out.exists()


def test_the_anthropic_reader_reads_only_the_cases_named(tmp_path, monkeypatch):
    """With --case the live option sends the documents of the cases named alone: the client is asked for no other document."""
    asked = []

    class Replayed(RecordedClient):
        def __init__(self, model, effort="medium"):
            super().__init__(RECORDED_DIR)
            self.model, self.effort = model, effort

        def complete(self, req):
            if req.pdf is not None:
                asked.append(hashlib.sha256(req.pdf).hexdigest())
            return super().complete(req)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "made-up-key-for-the-test")
    monkeypatch.setattr(demo, "AnthropicClient", Replayed)
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--reader", "anthropic", "--case", "MU05", "--case", "MU16"]) == 0
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == {"MU05": RECORDED_STATUS["MU05"],
                                                                                          "MU16": RECORDED_STATUS["MU16"]}
    named = {hashlib.sha256((demo.MADEUP_OUT / f"{case}_conf.pdf").read_bytes()).hexdigest() for case in ("MU05", "MU16")}
    assert set(asked) <= named


def test_a_prepared_folder_lets_the_loop_open_a_case_by_itself(tmp_path, capsys):
    """--prepare-only files the purchase orders and runs nothing; a confirmation dropped into the inbox is then a new case for the
    loop, not a duplicate."""
    import os
    import shutil
    from aid.demo import MADEUP_OUT
    out = tmp_path / "live"
    assert main(["--out", str(out), "--prepare-only"]) == 0
    assert not (out / "summary.csv").exists() and (out / "inbox").is_dir()
    assert "no case run" in capsys.readouterr().out
    settings = load_settings(out / "settings.json")
    assert settings.po_store_root.exists() and len(list(settings.po_store_root.iterdir())) >= 21
    # The made-up set sends nothing by its settings, not by the absence of a mailbox (ruling S4 of the review round of Task 44):
    # the prepared folder says send_via "file", so that a sign-in made on its settings tab sends nothing either, and the mailer of
    # the runner and of the page writes files by choice.
    mailer = build_deps(settings, {}).mailer
    assert settings.send_via == "file" and type(mailer).__name__ == "FileMailer" and mailer.unrouted is None
    assert mailer.out_dir == settings.cases_root / "outbox"
    shutil.copy(MADEUP_OUT / "MU03_conf.pdf", out / "inbox")
    deps = build_deps(settings, dict(os.environ))
    assert run_loop(deps, out / "STOP", poll_seconds=0, max_passes=1) == 1
    assert (out / "inbox" / "processed" / "MU03_conf.pdf").exists()
    folders = [p.name for p in settings.cases_root.iterdir() if p.name.startswith("250103_")]
    assert len(folders) == 1 and (settings.cases_root / folders[0] / "steps" / "08_result.json").exists()


def test_the_folder_of_the_demonstration_start_offers_the_try_out_and_writes_questions_as_files(tmp_path, capsys):
    """--demonstration, which the demonstration start of the folder version runs: the batch runs on the settings of every other run,
    and then the folder's settings are those of the page and the runner of the demonstration: the test mode on and no authorization
    limit, so that the settings tab offers a try-out and the watched folder takes one; every message written as a file
    (send_via "file"); a made-up address on example.com for each made-up supplier, so that a question can be asked; and those
    addresses and the buyer's as the own addresses of the test mode, so that its guard lets the question be written."""
    from aid.messaging import Message
    from aid.tryout_mark import offers_tryouts
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--demonstration"]) == 0
    expected = PARSER_STATUS | {"CART02": "review"}
    assert {row["case_id"]: row["status"] for row in read_csv(out / "summary.csv")} == expected      # the run as without the option
    settings = load_settings(out / "settings.json")
    assert settings.test_mode and settings.authorization_limit is None and offers_tryouts(settings)
    assert settings.send_via == "file" and settings.reader == "parser"
    assert settings.supplier_emails["S03"] == "s03@example.com" and len(settings.supplier_emails) == len(settings.supplier_lead_times)
    assert all(address.endswith("@example.com") for address in settings.supplier_emails.values())
    assert set(settings.own_addresses) == {settings.buyer_email, *settings.supplier_emails.values()}
    mailer = build_deps(settings, {}).mailer
    assert type(mailer).__name__ == "FileMailer" and mailer.unrouted is None and mailer.guard is not None
    mailer.send(Message(to=["s03@example.com"], cc=[], subject="Made-up question", body="Made-up body", template="question",
                        case_id="made-up"))
    assert len(list((out / "cases" / "outbox").glob("*.eml"))) == 1
    assert "demonstration" in capsys.readouterr().out


def test_the_demonstration_opens_the_price_list_tab_with_a_made_up_pair_of_files(tmp_path):
    """--demonstration also makes the tab "Prijslijst" work at once: the folder's settings name the columns of the export of articles
    (an installation step otherwise, part 3 of the block "Koppeling met Exact"), the purchase prices of S04 are taken in from a made-up
    export as the tab's "Inkoopprijzen opnemen" takes them in, and the columns of S04's price list are kept, so that the column fields
    fill themselves. The two made-up files lie in <out>/voorbeelden: "Bekijken" on the price list shows prices that stay the same, two
    that change by a few percent, an article that the export does not know, a price that cannot be read and one that differs so much
    that the tab keeps it off the list; the press puts the two changes on the list "In Exact invoeren". A plain demo folder has none of
    it: settings with these columns count as company settings and refuse a public link."""
    from aid.pricelist import current_prices, kept_columns
    from aid.review.page import build_app
    from aid.tests.test_hand_tab import (CARRIED_OUT, COLUMN_FIELDS, PRICELIST_OPENED, PRICELIST_SHOWN, SUPPLIER_CHOSEN, called)
    from aid.execution.by_hand import open_changes
    out = tmp_path / "demo"
    assert main(["--out", str(out), "--demonstration"]) == 0
    path = out / "settings.json"
    settings = load_settings(path)
    assert settings.article_columns == {"item_code": "Artikel", "price": "Inkoopprijs"} and settings.article_decimal_comma
    samples = out / demo.SAMPLES
    export, price_list = samples / demo.SAMPLE_EXPORT, samples / demo.SAMPLE_LIST
    assert demo.SAMPLES == "voorbeelden" and sorted(p.name for p in samples.iterdir()) == sorted([export.name, price_list.name])
    assert "verzonnen" in export.name.lower() and "verzonnen" in price_list.name.lower() and "S04" in export.name and "S04" in price_list.name
    assert export.suffix == price_list.suffix == ".xlsx"
    assert_like_real_workbooks(export, price_list)
    current, stored_at = current_prices(settings.cases_root, "S04")
    assert stored_at is not None and current["KM-0424"] == "0.85" and current["KM-0422"] == "2.85" and "KM-0400" not in current
    assert kept_columns(settings.cases_root) == {"S04": {"item_code": "Artikelnummer", "price": "Nettopreis", "basis": "Preiseinheit"}}

    page = build_app(build_deps(settings, {}), settings_file=path)
    opened = called(page, "pricelist_opened", PRICELIST_OPENED, None, "nl")
    assert opened["body"] == {"__type__": "update", "visible": True} and opened["supplier"]["choices"] == ["S04"]
    chosen = called(page, "pricelist_supplier_chosen", SUPPLIER_CHOSEN, "S04", str(price_list), "nl")
    assert chosen["list_part"] == {"__type__": "update", "visible": True}
    fields = [chosen[name]["value"] for name in COLUMN_FIELDS]
    assert fields == ["Artikelnummer", "Nettopreis", "Preiseinheit", "", ""]
    view = called(page, "pricelist_shown", PRICELIST_SHOWN, str(price_list), "S04", *fields, "nl")
    rows = {row[0]: row for row in view["table"]["data"]}
    outcomes = [row[-1] for row in view["table"]["data"]]
    assert outcomes.count("ongewijzigd") >= 3 and outcomes.count("prijs verandert") == 2
    assert rows["KM-0424"][1:] == ["0,85", "0,88", "prijs verandert"] and rows["KM-0406"][1:] == ["0,68", "0,70", "prijs verandert"]
    assert rows["KM-0400"][-1] == "artikel niet bekend in Exact" and rows["KM-0421"][-1] == "prijs niet te lezen"
    assert rows["KM-0422"][-1].startswith("wijkt sterk af")
    assert outcomes[-1] == "ongewijzigd" and len(outcomes) <= 9         # the rows that matter come first, in the view without a scroll
    assert view["note"].startswith("2 prijzen veranderen") and view["carry"] == {"__type__": "update", "interactive": True}
    carried = called(page, "pricelist_carried_out", CARRIED_OUT, str(price_list), "S04", *fields, view["viewed"], "nl")
    assert carried["note"].startswith("2 prijswijzigingen staan op de lijst In Exact invoeren.")
    listed = {(change.article_code, change.new_value) for change in open_changes(settings.cases_root / "prepared")
              if change.kind == "article_price" and change.case_ids[0].startswith("pricelist_S04_")}
    assert listed == {("KM-0406", "0.70"), ("KM-0424", "0.88")}


def assert_like_real_workbooks(export, price_list):
    """The two sample files look as such files look when the buyer opens them in Excel. The export of Exact holds its prices as
    number cells shown with two decimals, so Excel shows no "number stored as text" warning in it; none of them ends in a zero, as
    the tab shows a number cell's price without its trailing zero ("0,8"). The price list holds its prices as texts with the decimal
    comma, as a list a supplier converts from a PDF often does, so that the tab shows them as the list writes them ("0,70",
    "28,50"). It is that of S04, a German supplier (its confirmation of 250104 is in German), with German column titles and
    descriptions, while the export holds the buyer's Dutch descriptions; the row of KM-0422, a price per 10 read as a price per
    piece, has its price unit blank, as a supplier who forgot to fill it in leaves it, so that a buyer who checks the column the tab
    names finds the gap; and neither file names the library that wrote it as its author."""
    from openpyxl import load_workbook
    rows = {}
    for path in (export, price_list):
        book = load_workbook(path)
        assert book.properties.creator != "openpyxl" and "verzonnen" in book.properties.creator.lower()
        rows[path.name] = list(book.active.iter_rows())
    exported = {row[0].value: row for row in rows[export.name][3:]}
    assert [cell.value for cell in rows[export.name][2]] == ["Artikel", "Omschrijving", "Inkoopprijs"]
    assert exported["KM-0406"][1].value == "Zeskantbout M12 x 50"
    for row in exported.values():
        assert row[2].data_type == "n" and isinstance(row[2].value, float) and row[2].number_format == "0.00"
        assert not f"{row[2].value:.2f}".endswith("0")
    assert exported["KM-0422"][2].value == 2.85
    header, *listed = rows[price_list.name]
    assert [cell.value for cell in header] == ["Artikelnummer", "Bezeichnung", "Nettopreis", "Preiseinheit"]
    listed = {row[0].value: row for row in listed}
    assert listed["KM-0406"][1].value == "Sechskantschraube M12 x 50" and listed["KM-0425"][1].value == "Schlauchtülle 1/2 Zoll"
    for code, row in listed.items():
        assert row[2].data_type == "s"
        assert row[3].value == (None if code == "KM-0422" else 1)
    assert listed["KM-0422"][2].value == "28,50" and listed["KM-0406"][2].value == "0,70" and listed["KM-0421"][2].value == "auf Anfrage"


def test_a_plain_demo_folder_leaves_the_price_list_to_the_installation(tmp_path):
    """Without --demonstration the columns of the export of articles stay unset, nothing is taken in and no sample is written, so that
    the folder's settings still allow a public link of the review page."""
    from aid.review.__main__ import refuse_share
    out = tmp_path / "demo"
    assert main(["--out", str(out)]) == 0
    settings = load_settings(out / "settings.json")
    assert settings.article_columns == {} and refuse_share(settings) is None
    assert not (out / demo.SAMPLES).exists() and not (settings.cases_root / "exact").exists()
