"""Shared pytest setup of the checking aid: the working directory, the settings every test uses, the made-up set, and the
dependencies of a pipeline run on it."""
import csv
import os
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aid.settings import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

# SETTINGS is built while pytest imports this file, before any fixture has run, so its path comes from
# REPO_ROOT and not from the working directory. All values below are made up for the tests. In particular
# the authorization limit is a made-up test limit, not the buyer's real one, which is kept only in a
# settings.json outside the repository.
_example = load_settings(REPO_ROOT / "aid" / "settings.example.json")
SETTINGS = Settings.model_validate(_example.model_dump() | {
    "first_cases_review": 0,                                          # the new-supplier rule fires only where a test sets it
    "authorization_limit": Decimal("5000"),                           # made-up test limit
    "supplier_lead_times": {f"S{i:02d}": 10 for i in range(1, 23)},   # the made-up suppliers S01 to S22
})
# Each made-up set of the madeup_out fixture, with the folder of the PO store that deps() files from it once per session.
_PO_STORES: dict[Path, Path] = {}


@pytest.fixture(scope="session", autouse=True)
def run_from_repository_root():
    """Run every test from the repository root, as `python3 -m pytest aid/tests -q` does, so that paths
    such as Path("aid/settings.example.json") resolve however pytest was started."""
    previous = Path.cwd()
    os.chdir(REPO_ROOT)
    yield
    os.chdir(previous)


@pytest.fixture(scope="session")
def madeup_out(tmp_path_factory):
    """The made-up document set (aid/madeup), generated once per test session; returns the folder with the files. deps() files the
    set's PO PDFs in a folder of the session the first time it is called."""
    from aid.madeup.generate import generate

    out = tmp_path_factory.mktemp("madeup")
    generate(REPO_ROOT / "aid" / "madeup" / "spec", out)
    _PO_STORES[out] = tmp_path_factory.mktemp("po_store") / "store"
    return out


def read_csv(path) -> list[dict]:
    """The rows of a CSV file, read with the file closed again: a file left open for the garbage collector fails a test under -W
    error."""
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def deps(tmp_path, madeup_out, writer=None, settings=None):
    """The dependencies of a pipeline run on the made-up set (Deps of aid/pipeline.py): the settings (SETTINGS unless given) with the
    cases under tmp_path/cases; a PO store in tmp_path/store, read with the settings' authorization limit, holding every PO PDF of
    the set; the parser as the reader; no decider; the writer (a PreparedWriter into tmp_path unless given); no mailer; and a clock
    that stands at 12 November 2026, 9:00.

    The PO PDFs of the madeup_out fixture are filed once per session and each call copies that store, which takes a small part of
    the time filing does; the pdf_path of a version in the copy names the session's copy of its PDF, which holds the same bytes. A
    set from any other folder is filed for the call. A second call on the same tmp_path reuses its store and its cases."""
    from aid.decider import NoDecider
    from aid.execution.writer import PreparedWriter
    from aid.pipeline import Deps, read_with_parser
    from aid.po_store import PoStore

    settings = (settings or SETTINGS).model_copy(update={"cases_root": tmp_path / "cases"})
    root, filed = tmp_path / "store", _PO_STORES.get(madeup_out)
    if filed is None:
        _file_po_pdfs(madeup_out, root)
    else:
        if not filed.exists():
            _file_po_pdfs(madeup_out, filed)
        shutil.copytree(filed, root, dirs_exist_ok=True)      # a second call on one tmp_path reuses the copy
    return Deps(settings=settings, store=PoStore(root, settings.authorization_limit), reader=read_with_parser, decider=NoDecider(),
                writer=writer or PreparedWriter(tmp_path), mailer=None, clock=lambda: datetime(2026, 11, 12, 9, 0))


def _file_po_pdfs(madeup_out: Path, root: Path) -> None:
    """File every PO PDF of the made-up set into a PO store at root. A filing cut off by an error is removed, so that no later call
    copies half a store."""
    from aid.po_store import PoStore

    store = PoStore(root)
    try:
        for pdf in sorted(Path(madeup_out).glob("*_po_v*.pdf")):
            store.add(pdf)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
