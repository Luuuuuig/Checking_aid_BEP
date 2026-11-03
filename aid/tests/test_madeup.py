"""Tests of the made-up document set (aid/madeup): the specs, the generated files and the references."""
import csv
from pathlib import Path

import pytest

from aid.madeup.generate import MadeUpSpec, generate, load_specs, reference_rows
from aid.readers.pdf_text import extract_lines


def test_specs_validate():
    specs = [MadeUpSpec.model_validate_json(p.read_text()) for p in sorted(Path("aid/madeup/spec").glob("*.json"))]
    assert len(specs) == 22 and sum(1 for s in specs if s.confirmation) == 20


def test_generate_counts(tmp_path):
    files = generate(Path("aid/madeup/spec"), tmp_path)
    assert len(list(tmp_path.glob("MU*_po_v*.pdf"))) >= 20 and len(list(tmp_path.glob("MU*_conf.pdf"))) == 20
    assert (tmp_path / "CART01.html").exists() and (tmp_path / "CART02.html").exists() and (tmp_path / "CART02.png").exists() and (tmp_path / "CART02_export.csv").exists()


def test_references_consistent():
    with open("aid/madeup/references.csv", encoding="utf-8", newline="") as f:
        refs = list(csv.DictReader(f))
    specs = {s.case_id: s for s in load_specs(Path("aid/madeup/spec"))}
    for cid, s in specs.items():
        rows = [r for r in refs if r["case_id"] == cid]
        assert sum(1 for r in rows if r["item_type"] == "version") == 1
        for d in (s.confirmation.differences if s.confirmation else []):
            assert any(r["line_id"] == d.line_id and r["field"] == d.field and r["label"] != "same" for r in rows)


def test_references_match_the_specs():
    """The committed references.csv holds exactly the rows the specs give, the carts included, in order."""
    with open("aid/madeup/references.csv", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        committed = list(reader)
    assert reader.fieldnames == Path("evaluation/templates/references.csv").read_text(encoding="utf-8").strip().split(",")
    assert committed == [row for spec in load_specs(Path("aid/madeup/spec")) for row in reference_rows(spec)]


def test_contradicting_spec_writes_nothing(tmp_path):
    """A spec whose values do not give the differences it declares stops generate() before any file is written."""
    spec_dir, out = tmp_path / "spec", tmp_path / "out"
    spec_dir.mkdir()
    for path in Path("aid/madeup/spec").glob("*.json"):
        (spec_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    mu08 = (spec_dir / "MU08.json").read_text(encoding="utf-8")
    assert mu08.count('"unit_price": "12.70"') == 1           # the confirmed price; the difference stays declared
    (spec_dir / "MU08.json").write_text(mu08.replace('"unit_price": "12.70"', '"unit_price": "12.50"'), encoding="utf-8")
    with pytest.raises(ValueError, match="MU08"):
        generate(spec_dir, out)
    assert not out.exists()


def test_scan_has_no_text_layer(tmp_path):
    generate(Path("aid/madeup/spec"), tmp_path)
    assert extract_lines(tmp_path / "MU05_conf.pdf") == []
