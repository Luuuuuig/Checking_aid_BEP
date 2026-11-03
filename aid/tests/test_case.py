import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from aid.case import find_case_by_hash, load_case, open_case, sha256_file


def test_open_case_copies_and_hashes(tmp_path):
    f = tmp_path / "conf.pdf"; f.write_bytes(b"%PDF-1.4 made up")
    c = open_case(tmp_path / "cases", "confirmation", [f], "250123", datetime(2026, 11, 3, 9, 0))
    assert c.case_id == "250123_20261103T090000"
    assert (c.root / "input" / "conf.pdf").read_bytes() == f.read_bytes()
    meta = json.loads((c.root / "case.json").read_text())
    assert meta["files"][0]["sha256"] == sha256_file(f)
    assert find_case_by_hash(tmp_path / "cases", sha256_file(f)).case_id == c.case_id


def test_write_and_read_step(tmp_path):
    c = open_case(tmp_path, "confirmation", [], None, datetime(2026, 11, 3, 9, 0))
    c.write_step(1, "read_po", {"a": 1})
    assert (c.root / "steps" / "01_read_po.json").exists() and c.read_step("read_po") == {"a": 1}


# The tests below cover what the brief's rulings and the case.json format add to the two above.

NOW = datetime(2026, 11, 3, 9, 0)


def put(folder: Path, name: str, content: bytes) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class Sample(BaseModel):
    price: Decimal
    when: datetime
    note: str


def test_sha256_file_gives_the_standard_digest(tmp_path):
    f = put(tmp_path, "abc.txt", b"abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_case_json_describes_the_case(tmp_path):
    content = b"%PDF-1.4 made up"
    f = put(tmp_path, "conf.pdf", content)
    c = open_case(tmp_path / "cases", "confirmation", [f], "250123", NOW)
    meta = json.loads((c.root / "case.json").read_text(encoding="utf-8"))
    assert meta == {"case_id": "250123_20261103T090000", "kind": "confirmation", "po_number": "250123",
                    "opened_at": "2026-11-03T09:00:00",
                    "files": [{"name": "conf.pdf", "sha256": sha256_file(f), "bytes": len(content)}]}
    assert [(c.root / d).is_dir() for d in ("input", "steps", "messages")] == [True, True, True]


def test_load_case_and_find_case_by_hash_return_the_case_open_case_made(tmp_path):
    cases = tmp_path / "cases"
    a = put(tmp_path, "a.pdf", b"first")
    b = put(tmp_path, "b.png", b"second")
    c = open_case(cases, "cart", [a, b], "250123", NOW)
    assert (c.kind, c.po_number) == ("cart", "250123")
    assert c.input_files == [c.root / "input" / "a.pdf", c.root / "input" / "b.png"]
    assert load_case(cases, c.case_id) == c
    assert find_case_by_hash(cases, sha256_file(b)) == c
    no_po = open_case(cases, "confirmation", [], None, NOW)
    assert no_po.case_id == "unknown_20261103T090000" and no_po.po_number is None
    assert load_case(cases, no_po.case_id) == no_po


def test_find_case_by_hash_finds_nothing_when_nothing_matches(tmp_path):
    cases = tmp_path / "cases"
    assert find_case_by_hash(cases, "0" * 64) is None                  # the folder does not exist yet
    f = put(tmp_path, "conf.pdf", b"made up")
    open_case(cases, "confirmation", [f], "250123", NOW)
    (cases / "review_items.jsonl").write_text("{}\n")                  # a file that is not a case
    (cases / "half_made").mkdir()                                      # a folder without a case.json
    assert find_case_by_hash(cases, "0" * 64) is None
    assert find_case_by_hash(cases, sha256_file(f)) is not None        # and none of it hides a real match


def test_write_step_writes_a_model_as_json(tmp_path):
    c = open_case(tmp_path, "confirmation", [], None, NOW)
    path = c.write_step(2, "sample", Sample(price=Decimal("12.50"), when=NOW, note="één"))
    assert path == c.root / "steps" / "02_sample.json"
    assert path.read_text(encoding="utf-8") == '{\n "price": "12.50",\n "when": "2026-11-03T09:00:00",\n "note": "één"\n}\n'
    assert c.read_step("sample") == {"price": "12.50", "when": "2026-11-03T09:00:00", "note": "één"}


def test_read_step_matches_the_whole_step_name(tmp_path):
    c = open_case(tmp_path, "confirmation", [], None, NOW)
    c.write_step(1, "version", {"which": "version"})
    c.write_step(2, "po_version", {"which": "po_version"})           # the higher number must not win for "version"
    assert c.read_step("version") == {"which": "version"}
    assert c.read_step("po_version") == {"which": "po_version"}
    with pytest.raises(FileNotFoundError): c.read_step("missing")


def test_open_case_refuses_a_second_case_with_the_same_id(tmp_path):
    cases = tmp_path / "cases"
    c = open_case(cases, "confirmation", [put(tmp_path, "one.pdf", b"one")], "250123", NOW)
    with pytest.raises(FileExistsError):
        open_case(cases, "confirmation", [put(tmp_path, "two.pdf", b"two")], "250123", NOW)
    assert [p.name for p in (c.root / "input").iterdir()] == ["one.pdf"]       # the first case is untouched


@pytest.mark.parametrize("kind, po_number, names", [
    ("confirmaton", "250123", ["a.pdf"]),               # a kind that does not exist
    ("confirmation", "250/123", ["a.pdf"]),             # a PO number that cannot be part of a folder name
    ("confirmation", "250123", ["a.pdf", "x/a.pdf"]),   # two input files with the same name
], ids=["unknown kind", "PO number with a slash", "same file name twice"])
def test_open_case_refuses_input_it_cannot_store_faithfully(tmp_path, kind, po_number, names):
    files = [put(tmp_path, name, b"x") for name in names]
    with pytest.raises(ValueError):
        open_case(tmp_path / "cases", kind, files, po_number, NOW)
    assert not (tmp_path / "cases").exists()                                    # nothing was created


def test_open_case_with_a_missing_file_creates_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_case(tmp_path / "cases", "confirmation", [tmp_path / "gone.pdf"], "250123", NOW)
    assert not (tmp_path / "cases").exists()


def test_open_case_keeps_the_modification_time_of_a_file(tmp_path):
    f = put(tmp_path, "cart.html", b"<html></html>")
    os.utime(f, (1_700_000_000, 1_700_000_000))        # a saved cart page is dated by its modification time
    c = open_case(tmp_path / "cases", "cart", [f], "250123", NOW)
    assert c.input_files[0].stat().st_mtime == 1_700_000_000


@pytest.mark.parametrize("case_id", ["../other", "..", "a/b", "/elsewhere", ""])
def test_load_case_refuses_an_id_that_is_not_one_folder_name(tmp_path, case_id):
    with pytest.raises(ValueError):
        load_case(tmp_path / "cases", case_id)


@pytest.mark.parametrize("content", ['{"case_id": "250101_2026', '[]', '{"case_id": "x"}', b'\xff\xfe not text'])
def test_a_case_json_that_cannot_be_read_is_skipped_by_the_search_and_refused_by_load_case(tmp_path, content):
    """One damaged folder must not stop the intake: find_case_by_hash goes on to the cases after it, and load_case raises a
    ValueError that names the folder, which the runner logs once."""
    cases = tmp_path / "cases"
    damaged = cases / "250100_20261112T090000"; damaged.mkdir(parents=True)
    (damaged / "case.json").write_bytes(content if isinstance(content, bytes) else content.encode())
    c = open_case(cases, "confirmation", [put(tmp_path, "a.pdf", b"%PDF-1.4 a")], "250101", NOW)
    assert find_case_by_hash(cases, sha256_file(c.input_files[0])) == c
    with pytest.raises(ValueError, match=damaged.name):
        load_case(cases, damaged.name)
