"""Tests of the safe copy into the watched folder (aid/sources/dropin.py), by which the review page gives a file to the assistant: the
try-out confirmation of the part 'Proefdraaien' (aid/tryout.py), and the files given on the page (Task 37). Every file is made up."""
import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from aid.po_store import PoStore
from aid.sources import dropin
from aid.sources.dropin import drop
from aid.sources.mailbox import NAME_BYTES, scan_folder
from aid.tests.conftest import SETTINGS

CONTENT = b"%PDF-1.4 made-up try-out confirmation"


def source(tmp_path: Path, content: bytes = CONTENT, name: str = "made.pdf") -> Path:
    path = tmp_path / "made" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


def test_drop_gives_a_safe_free_name_and_never_half_a_file(tmp_path, monkeypatch):
    """The file lands in the watched folder under its own name made safe as the mailbox makes an attachment's name (its last part
    after any folder, the characters that file systems refuse made "_", no dots or spaces at its ends, at most NAME_BYTES bytes before
    its extension kept), with _2, _3 when the name is taken. It is written first under a name that starts with a dot, which the scan
    leaves alone, and renamed once it is whole; a copy that fails leaves nothing behind."""
    inbox, made = tmp_path / "inbox", source(tmp_path)
    renames, rename = [], os.rename
    settings = SETTINGS.model_copy(update={"cases_root": tmp_path / "cases", "po_store_root": tmp_path / "store", "watched_folder": inbox})

    def watched(old, new):
        """The rename that puts the file in place, from a name with a dot in the watched folder, once the file is whole; a scan of the
        folder before it (at the first drop, when the folder holds nothing else) reads nothing, skips nothing and moves nothing."""
        old, new = Path(old), Path(new)
        if old.parent == inbox and old.name.startswith("."):
            assert old.read_bytes() == CONTENT and not new.exists()
            if not renames:
                scanned = scan_folder(inbox, PoStore(settings.po_store_root), settings.cases_root, settings, datetime(2026, 11, 12, 9, 0))
                assert (scanned.new_cases, scanned.skipped, scanned.stored_pos) == ([], [], [])
                assert [path.name for path in inbox.iterdir()] == [old.name]
            renames.append((old, new))
        rename(old, new)

    monkeypatch.setattr(dropin.os, "rename", watched)
    first = drop(made, "../map\\PROEF: order*bevestiging? <1>.pdf. ", inbox)        # the watched folder is made when it is not there
    assert first == inbox / "PROEF_ order_bevestiging_ _1_.pdf" and first.read_bytes() == CONTENT
    second = drop(made, "../map\\PROEF: order*bevestiging? <1>.pdf. ", inbox)
    third = drop(made, "PROEF_ order_bevestiging_ _1_.pdf", inbox)
    assert (second.name, third.name) == ("PROEF_ order_bevestiging_ _1__2.pdf", "PROEF_ order_bevestiging_ _1__3.pdf")
    assert [new for _, new in renames] == [first, second, third] and len({old for old, _ in renames}) == 3
    assert sorted(path.name for path in inbox.iterdir()) == sorted([first.name, second.name, third.name])     # no dot file is left
    assert made.read_bytes() == CONTENT                                                                       # the source stays
    # a name that is too long is cut as the mailbox cuts it; a name without an extension takes the source's; a name of nothing but
    # dots and spaces is "file"
    long = drop(made, "a" * 300 + ".pdf", inbox)
    assert long.name == "a" * (NAME_BYTES - 4) + ".pdf" and len(long.name.encode()) == NAME_BYTES
    assert drop(made, "proef", inbox).name == "proef.pdf" and drop(made, " .. ", inbox).name == "file.pdf"
    # a copy that fails halfway leaves no file, not even the one under the dot name
    before = sorted(path.name for path in inbox.iterdir())

    def broken(*args, **kwargs):
        raise OSError("the disk is full")

    monkeypatch.setattr(dropin.shutil, "copyfileobj", broken)
    with pytest.raises(OSError):
        drop(made, "PROEF.pdf", inbox)
    assert sorted(path.name for path in inbox.iterdir()) == before


def test_an_empty_file_is_refused(tmp_path):
    """An empty file is refused with ValueError, which names no file and no value, and nothing is written: no file and no folder."""
    inbox = tmp_path / "inbox"
    with pytest.raises(ValueError) as refused:
        drop(source(tmp_path, b""), "PROEF orderbevestiging 250108 20261112-090000.pdf", inbox)
    assert "empty" in str(refused.value) and "250108" not in str(refused.value) and not inbox.exists()
    inbox.mkdir()
    with pytest.raises(ValueError):
        drop(source(tmp_path, b"", "leeg.pdf"), "leeg.pdf", inbox)
    assert list(inbox.iterdir()) == []
    shutil.rmtree(tmp_path / "made")
    with pytest.raises(FileNotFoundError):                    # a source that is gone is no empty file: its own error
        drop(tmp_path / "made" / "gone.pdf", "gone.pdf", inbox)
    assert list(inbox.iterdir()) == []


def test_the_leftovers_of_a_drop_cut_off_go_after_ten_minutes(tmp_path, monkeypatch):
    """Ruling Y15 of Task 33: a drop cut off in the middle of its copy (the page ended, the PC switched off) leaves its temporary file,
    whose name starts with a dot, in the watched folder, where the scan never reads it. remove_leftovers removes such a file of drop's
    own (.dropin_<...>.part) once it is older than ten minutes, by its modification time, so that a drop in progress is left; any other
    file, a dot file of another program among them, stays. A file that cannot be removed is returned with its error."""
    folder = tmp_path / "inbox"
    folder.mkdir()
    now = datetime(2026, 11, 12, 9, 0).timestamp()
    old, young = folder / ".dropin_k3j2h1.part", folder / ".dropin_a1b2c3.part"
    kept = [young, folder / ".DS_Store", folder / ".dropin_notes.txt", folder / "dropin_x.part", folder / "MU08_conf.pdf"]
    for path, age in ((old, 601), *((path, 3600) for path in kept[1:]), (young, 599)):
        path.write_bytes(CONTENT)
        os.utime(path, (now - age, now - age))
    assert dropin.LEFTOVER_SECONDS == 600
    assert dropin.remove_leftovers(folder, now) == [] and not old.exists() and all(path.exists() for path in kept)
    assert dropin.remove_leftovers(tmp_path / "missing", now) == []
    os.utime(young, (now - 700, now - 700))

    def refused(self, *args, **kwargs):
        raise PermissionError(13, "made-up: in use")
    monkeypatch.setattr(Path, "unlink", refused)
    [error] = dropin.remove_leftovers(folder, now)
    assert isinstance(error, PermissionError) and young.exists()
    assert (dropin.PREFIX, dropin.SUFFIX) == (".dropin_", ".part")          # the name drop writes its copy under
