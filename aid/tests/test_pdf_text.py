"""Tests of the text layer (aid/readers/pdf_text.py) on the made-up PDFs."""
import csv
import io
import re
from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from aid.madeup.generate import answered_version, load_specs, po_pages, row_positions
from aid.readers.pdf_text import TextLine, extract_lines, extract_rows, find_source, page_count, render_page

SPEC_DIR = Path(__file__).resolve().parents[1] / "madeup" / "spec"


def test_extract_lines_numbers_per_page(madeup_out):
    lines = extract_lines(madeup_out / "MU04_po_v1.pdf")       # MU04: 32 lines over 2 pages
    assert lines[0].page == 1 and lines[0].line == 1 and any(l.page == 2 and l.line == 1 for l in lines)


# --- numbering and joining ---

def test_lines_run_from_one_on_each_page_from_top_to_bottom(madeup_out):
    lines = extract_lines(madeup_out / "MU04_po_v1.pdf")
    for page, count in ((1, 32), (2, 8)):          # page 1: six header rows, the title row and 25 lines; page 2: the title row and 7 lines
        on_page = [l for l in lines if l.page == page]
        assert [l.line for l in on_page] == list(range(1, count + 1))
        assert [l.top for l in on_page] == sorted(l.top for l in on_page)
    assert [l.page for l in lines] == sorted(l.page for l in lines)


def test_a_line_joins_its_words_by_single_spaces_in_x_order(madeup_out):
    lines = {(l.page, l.line): l for l in extract_lines(madeup_out / "MU01_po_v1.pdf")}
    assert sorted(lines) == [(1, n) for n in range(1, 11)]                  # six header rows, the title row and three lines
    assert lines[1, 1].text == "Inkooporder 250101"
    assert lines[1, 7].text == "Regel Artikel Omschrijving Aantal Eenheid Prijs Per Leverdatum"
    assert lines[1, 8].text == "1 KM-1001 Hydraulische slang 10 mm, 2 m 10 st 12,50 1 16-11-2026"
    assert lines[1, 8].x0 == pytest.approx(50.0, abs=0.5)                   # the left margin
    assert lines[1, 6].top < lines[1, 7].top < lines[1, 8].top


def test_words_a_few_points_apart_share_a_line_and_x_order_wins(tmp_path):
    """The tolerance is 3 points on the top of a word. The words of a line read from left to right whatever their heights,
    and the top of the line is the top of its highest word."""
    path = tmp_path / "lines.pdf"
    canvas = Canvas(str(path), pagesize=A4)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(50, 700, "one")
    canvas.drawString(150, 702, "two")                # the highest word of the line, 2 points above "one"
    canvas.drawString(250, 700.5, "three")
    canvas.drawString(50, 688, "below")              # 14 points under "two": the next line
    canvas.showPage()
    canvas.drawString(50, 700, "second page")
    canvas.save()
    lines = extract_lines(path)
    assert [(l.page, l.line, l.text) for l in lines] == [(1, 1, "one two three"), (1, 2, "below"), (2, 1, "second page")]
    assert lines[0].x0 == pytest.approx(50.0, abs=0.5) and lines[1].top - lines[0].top == pytest.approx(14.0, abs=0.01)


def test_extract_rows_keep_the_words_of_each_line_with_their_positions(madeup_out):
    path = madeup_out / "MU01_po_v1.pdf"
    rows = extract_rows(path)
    assert [row.line for row in rows] == extract_lines(path)
    assert all(" ".join(w.text for w in row.words) == row.line.text for row in rows)
    title = rows[6]                                  # the column titles sit at the x positions of the po_madeup layout
    assert [w.text for w in title.words] == ["Regel", "Artikel", "Omschrijving", "Aantal", "Eenheid", "Prijs", "Per", "Leverdatum"]
    assert [round(w.x0) for w in title.words] == [50, 82, 140, 310, 352, 397, 452, 482]
    assert all(w.x0 < w.x1 for row in rows for w in row.words)


def test_page_count(madeup_out):
    assert page_count(madeup_out / "MU01_po_v1.pdf") == 1 and page_count(madeup_out / "MU04_po_v1.pdf") == 2
    assert page_count(madeup_out / "MU05_conf.pdf") == 1                    # a scan has a page and no text


@pytest.mark.parametrize("name", ["MU05_conf.pdf", "MU16_conf.pdf"])
def test_a_scan_has_no_text_layer(madeup_out, name):
    assert extract_lines(madeup_out / name) == [] and extract_rows(madeup_out / name) == []


# --- the numbering of Task 4's references ---

def _reference_rows():
    with open("aid/madeup/references.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_line_numbers_agree_with_the_references(madeup_out):
    """references.csv names every source as "<file>, page p, line l", the text rows of a page counted from 1. The row it names
    must hold what the reference is about: the PO number, the Incoterm, or the item code of the line."""
    specs = {s.case_id: s for s in load_specs(SPEC_DIR)}
    texts, checked = {}, set()
    for ref in _reference_rows():
        source = re.fullmatch(r"(?P<file>\w+\.pdf), page (?P<page>\d+), line (?P<line>\d+)", ref["source"])
        if source is None:                                                  # "po" and the rows of a cart page
            continue
        spec = specs[ref["case_id"]]
        if spec.confirmation.layout == "scan":                              # a scan has no text layer to number
            continue
        if source["file"] not in texts:
            texts[source["file"]] = {(l.page, l.line): l.text for l in extract_lines(madeup_out / source["file"])}
        if ref["item_type"] == "version":
            expected = spec.po.po_number
        elif ref["item_type"] == "required information":
            expected = spec.confirmation.incoterm
        elif ref["line_label"] == "extra":
            expected = next(l.item_code for l in spec.confirmation.lines if l.line_id == ref["line_id"])
        else:
            expected = next(p.item_code for p in answered_version(spec).lines if p.line_id == ref["line_id"])
        row = texts[source["file"]].get((int(source["page"]), int(source["line"])))
        assert row is not None and expected in row, f"{ref['reference_item_id']}: {ref['source']} holds {row!r}, not {expected!r}"
        checked.add(ref["case_id"])
    assert checked == {s.case_id for s in specs.values() if s.confirmation and s.confirmation.layout != "scan"}


def test_po_line_numbers_follow_the_drawing_order(madeup_out):
    """Every PO PDF of the set: the row the generator draws a PO line on is the line extract_lines numbers it."""
    files = 0
    for spec in (s for s in load_specs(SPEC_DIR) if s.confirmation):
        for version in spec.po.versions:
            text = {(l.page, l.line): l.text for l in extract_lines(madeup_out / f"{spec.case_id}_po_v{version.version}.pdf")}
            positions = row_positions(po_pages(spec, version))
            assert spec.po.po_number in text[positions["po_reference"]]
            for po in version.lines:
                assert po.item_code in text[positions[f"line {po.line_id}"]], f"{spec.case_id} v{version.version} line {po.line_id}"
            files += 1
    assert files == len(list(madeup_out.glob("MU*_po_v*.pdf"))) == 21


def test_mu01_item_codes_sit_on_the_rows_the_references_name(madeup_out):
    """MU01, written out: the confirmation's lines are rows 8 to 10 of page 1 (the references say so), and so are the PO's."""
    refs = [r for r in _reference_rows() if r["case_id"] == "MU01" and r["item_type"] == "line presence"]
    assert [r["source"] for r in refs] == [f"MU01_conf.pdf, page 1, line {n}" for n in (8, 9, 10)]
    for name in ("MU01_conf.pdf", "MU01_po_v1.pdf"):
        text = {(l.page, l.line): l.text for l in extract_lines(madeup_out / name)}
        for row, code in ((8, "KM-1001"), (9, "KM-1002"), (10, "KM-1003")):
            assert code in text[1, row], f"{name} line {row}"
    po = {(l.page, l.line): l.text for l in extract_lines(madeup_out / "MU01_po_v1.pdf")}
    assert [po[1, n] for n in range(2, 7)] == ["Versie 1", "Datum 19-10-2026", "Leverancier: S01", "Levering: DAP Rotterdam", "Valuta: EUR"]


# --- find_source ---

def _line(page, line, text):
    return TextLine(page=page, line=line, text=text, x0=50.0, top=60.0 + 15 * line)


LINES = [_line(1, 1, "Inkooporder 250101"), _line(1, 2, "Leverancier: S01"), _line(2, 1, "Leverancier: S01"), _line(2, 2, "1 KM-1001  Hydraulische   slang")]


def test_find_source_ignores_case_and_runs_of_white_space():
    assert find_source(LINES, 2, "km-1001 HYDRAULISCHE slang") is LINES[3]
    assert find_source(LINES, 1, "leverancier:   s01") is LINES[1]
    assert find_source(LINES, 1, "  Inkooporder\u00a0250101 \n") is LINES[0]


def test_find_source_looks_on_the_given_page_only():
    assert find_source(LINES, 1, "Hydraulische") is None
    assert find_source(LINES, 3, "Inkooporder") is None
    assert find_source(LINES, 2, "Leverancier") is LINES[2]                 # the same text on page 1 is not taken


def test_find_source_takes_the_first_line_that_holds_the_text():
    twice = [_line(1, 1, "KM-1001 a"), _line(1, 2, "KM-1001 b")]
    assert find_source(twice, 1, "km-1001") is twice[0]


def test_find_source_finds_nothing_for_text_that_is_not_there_or_is_empty():
    assert find_source(LINES, 1, "KM-9999") is None
    assert find_source(LINES, 1, "") is None and find_source(LINES, 1, "  \t ") is None      # an empty text verifies nothing
    assert find_source([], 1, "Inkooporder") is None


def test_find_source_reads_composed_and_decomposed_letters_alike():
    lines = [_line(1, 1, "Auftragsbesta\u0308tigung")]                        # "a" and a combining diaeresis, as some PDFs write it
    assert find_source(lines, 1, "AUFTRAGSBEST\u00c4TIGUNG") is lines[0]


def test_find_source_on_a_real_page(madeup_out):
    found = find_source(extract_lines(madeup_out / "MU04_po_v1.pdf"), 2, "km-0432")
    assert found is not None and (found.page, found.line) == (2, 8)


# --- render_page ---

def _a4_png(png: bytes, dpi: int) -> Image.Image:
    """The PNG as an image, after checking that it is a PNG of an A4 page at this resolution."""
    image = Image.open(io.BytesIO(png))
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and image.format == "PNG"
    assert abs(image.width - 595.28 * dpi / 72) <= 1 and abs(image.height - 841.89 * dpi / 72) <= 1
    return image


def test_render_page_defaults_to_150_dpi(madeup_out):
    image = _a4_png(render_page(madeup_out / "MU01_po_v1.pdf", 1), 150)
    assert image.size == (1241, 1754)
    lowest, highest = image.convert("L").getextrema()
    assert lowest < 64 and highest == 255                                   # black text on white paper


@pytest.mark.parametrize("dpi", [72, 100])
def test_render_page_at_a_given_dpi(madeup_out, dpi):
    _a4_png(render_page(madeup_out / "MU01_po_v1.pdf", 1, dpi=dpi), dpi)


def test_render_page_renders_the_page_asked_for(madeup_out):
    def dark_pixels(page):
        return sum(Image.open(io.BytesIO(render_page(madeup_out / "MU04_po_v1.pdf", page, 72))).convert("L").histogram()[:128])
    assert dark_pixels(1) > dark_pixels(2) > 0                              # page 1 holds 32 lines of text, page 2 holds 8


def test_render_page_refuses_a_page_outside_the_document(madeup_out):
    for page in (0, 3, -1):
        with pytest.raises(ValueError, match="page"):
            render_page(madeup_out / "MU04_po_v1.pdf", page)


def test_render_page_renders_a_scan(madeup_out):
    image = _a4_png(render_page(madeup_out / "MU05_conf.pdf", 1), 150)
    assert image.convert("L").getextrema()[0] < 128                         # the scan has dark marks
