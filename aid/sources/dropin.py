"""A file given to the assistant on the review page: put into the watched folder, safely, for the runner's next pass.

drop copies a file into the watched folder (settings.watched_folder) under its own name, made safe as the mailbox makes the name of an
attachment (safe_name of aid/sources/mailbox.py), with _2, _3 and so on before its extension when the folder holds that name already.
The scan of the watched folder (scan_folder of aid/sources/mailbox.py) leaves alone a file whose name starts with a dot, so the copy is
written first under such a name of its own in the watched folder and renamed once it is whole: the scan never reads half a file. A
copy that fails removes its file again, and an empty file is refused before anything is written. From the watched folder on the file
is the runner's, as any file the mailbox rule saves there: the page writes nothing into the PO store or into a case. The part
"Proefdraaien" of the settings tab gives its try-out confirmation (aid/tryout.py) this way, and any other file given on the page is to
take the same way.

A drop cut off in the middle of its copy (the page ended, the PC switched off) leaves its copy under that name in the watched folder,
where the scan never reads it. remove_leftovers removes such a copy, a file whose name is drop's own (PREFIX, random letters, SUFFIX),
once it is older than LEFTOVER_SECONDS, ten minutes, by its modification time, so that a drop in progress is left; the runner calls it
at its start, as it empties <cases_root>/tmp/work (aid/runner.py). Any other file stays, a dot file of another program among them.
"""
import os
import shutil
import tempfile
import time
from pathlib import Path

from aid.sources.mailbox import _free_name, safe_name      # _free_name: the rule of _2, _3 by which the scan names a file in processed/

FALLBACK = "file"                                # the name of a file whose given name holds nothing that can be written
PREFIX, SUFFIX = ".dropin_", ".part"             # the name of drop's copy while it is not whole: the scan reads no name with a dot first
LEFTOVER_SECONDS = 600                           # how old such a copy is when remove_leftovers takes it for one a drop cut off left


def drop(source: Path, name: str, watched_folder: Path) -> Path:
    """Copy `source` into the watched folder under `name` made safe (with the extension of `source` when it has none), the first free
    name of _2, _3 when that one is taken, and return the file in the watched folder. The folder is made when it is not there. The copy
    is written under a name that starts with a dot and renamed when it is whole; a copy that fails leaves no file. ValueError for an
    empty file, before anything is written; the error names no file and no value. A source that cannot be read raises its own error."""
    source, folder = Path(source), Path(watched_folder)
    if source.stat().st_size == 0:
        raise ValueError("the file is empty, so it is not given to the assistant")
    wanted = safe_name(name, FALLBACK, source.suffix)
    folder.mkdir(parents=True, exist_ok=True)
    handle, partial = tempfile.mkstemp(prefix=PREFIX, suffix=SUFFIX, dir=folder)
    try:
        with os.fdopen(handle, "wb") as copy, open(source, "rb") as original:
            shutil.copyfileobj(original, copy)
        while True:
            target = folder / _free_name(wanted, lambda candidate: (folder / candidate).exists())
            try:
                os.rename(partial, target)        # on Windows a name taken in the meantime raises, and the next free one is tried
            except FileExistsError:
                continue
            return target
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise


def remove_leftovers(watched_folder: Path, now: float | None = None) -> list[OSError]:
    """Remove from the watched folder the copies that drops cut off left there (see the module docstring): the files named PREFIX ...
    SUFFIX whose modification time is more than LEFTOVER_SECONDS before `now`, in seconds since the epoch (the time of the PC when
    not given, as the file's time is). Returns the error of each copy that could not be removed, and of a folder that cannot be read;
    a folder that is not there holds none."""
    folder, now = Path(watched_folder), time.time() if now is None else now
    try:
        entries = list(folder.iterdir())
    except FileNotFoundError:
        return []
    except OSError as error:
        return [error]
    failed: list[OSError] = []
    for path in entries:
        if not (path.name.startswith(PREFIX) and path.name.endswith(SUFFIX)):
            continue
        try:
            if path.is_file() and not path.is_symlink() and now - path.stat().st_mtime > LEFTOVER_SECONDS:
                path.unlink()
        except OSError as error:
            failed.append(error)
    return failed
