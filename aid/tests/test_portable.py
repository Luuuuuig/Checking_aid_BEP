"""The aid from a folder (aid/portable.py): the build of the folder with a fake download of the embeddable Python and a fake pip,
the start files, the first start, and the start, the stop and the end of the start at logon with a fake run of the launcher and
fake message boxes. No test reaches the network, runs pip or reaches Windows, on any PC: the Windows calls are the fakes of these
tests, the fixture no_real_build makes the PC look like no Windows to a build with its defaults, whose real download and real pip
fail the test, and the fixture no_real_box fails a test that reaches the message boxes of Windows."""
import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
import types
import urllib.error
import zipfile
from pathlib import Path

import pytest

from aid import portable
from aid.settings import load_settings
from aid.tests.conftest import REPO_ROOT

VERSION = (3, 11, 9)                             # a version whose release page gives no SHA-256: it is not in EMBED_SHA256
URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
PAGE, CAPTURE = "http://127.0.0.1:7860/", "http://127.0.0.1:7870/health"
CMD_FILES = {"Inkoopassistent starten.cmd": "--start", "Inkoopassistent stoppen.cmd": "--stop",
             "Inkoopassistent niet meer vanzelf starten.cmd": "--no-autostart"}
DEMO_FILES = {"Demonstratie starten.cmd": "--demo", "Demonstratie stoppen.cmd": "--demo-stop"}
FOLDER = sorted([*CMD_FILES, *DEMO_FILES, "LEESMIJ.txt", "python", "app"])
ORIGINAL_PTH = b"python311.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"
LEESMIJ = ("Deze map is de inkoopassistent. Buiten deze map zet de assistent alleen een snelkoppeling in de map Opstarten en, als u "
           "zich aanmeldt bij Microsoft, de aanmelding in uw eigen profiel. Komt de assistent als zip-bestand van internet, ook een "
           "nieuwe versie, hef dan de blokkering op voordat u het uitpakt: klik met de rechtermuisknop op het zip-bestand, kies "
           "Eigenschappen, vink Blokkering opheffen aan en klik op OK. Anders kan Windows de assistent tegenhouden. Hebt u het al "
           "uitgepakt, stop dan de assistent als die draait en verwijder wat u uitpakte, maar nooit een map data; hef daarna de "
           "blokkering alsnog op en pak het zip-bestand opnieuw uit. Starten: dubbelklik op "
           "Inkoopassistent starten. Er blijft geen venster open: een melding zegt dat de assistent start, en na ongeveer een halve "
           "minuut sluit die en opent de pagina in uw browser (de eerste keer kan dat een paar minuten duren). Daarna start de "
           "assistent vanzelf wanneer u zich op deze pc aanmeldt. Lukt het starten niet, dan staat in een melding wat u kunt doen. "
           "Dubbelklikt u nog eens terwijl de assistent start, dan gebeurt er niets: de eerste start opent de pagina. Hebt u zich "
           "afgemeld of is de pc opnieuw opgestart terwijl de assistent startte, dan doet een dubbelklik tot acht minuten na de eerste "
           "dubbelklik niets: dubbelklik daarna opnieuw. "
           "Stoppen: dubbelklik op Inkoopassistent stoppen; de assistent blijft uit tot u hem weer start. "
           "Demonstratie: dubbelklik op Demonstratie starten om de assistent te laten zien met 22 verzonnen zaken, zonder AI en "
           "zonder verbinding. Alles in de map demo is verzonnen, en de map data wordt er niet voor gebruikt. Elke start begint "
           "opnieuw: de vorige map demo wordt vervangen. De assistent en de demonstratie draaien niet tegelijk: stop eerst de "
           "assistent met Inkoopassistent stoppen. Stoppen: dubbelklik op Demonstratie stoppen. "
           "Voor het tabblad Prijslijst staan in de map voorbeelden in de map demo een verzonnen export van artikelen uit Exact en "
           "een verzonnen prijslijst van leverancier S04; het tabblad is al ingesteld: kies S04, geef de prijslijst en druk op "
           "Bekijken. In de map "
           "data staan de instellingen en alle documenten die de assistent verwerkt: kopieer of verstuur die map niet. Zet deze map op "
           "de eigen schijf van de pc, niet in een map die met OneDrive wordt gesynchroniseerd; verplaatst u de map, dubbelklik dan "
           "opnieuw op Inkoopassistent starten. "
           r"Kies een kort pad zoals C:\Inkoopassistent, want Windows kan paden van meer dan 260 tekens weigeren. "
           "Bijwerken: stop de assistent, verwijder in deze map alles behalve de map data, zet "
           "alles wat in de map van de nieuwe versie staat in deze map, ook de bestanden waarop u dubbelklikt, en start opnieuw; de "
           "map data blijft staan. Het zip-bestand van een nieuwe versie bevat een map Inkoopassistent: pak het eerst uit op een "
           "andere plek, bijvoorbeeld in Downloads, en zet wat in die map Inkoopassistent staat in deze map, niet die map zelf. "
           "Weggooien: meld u op de pagina af bij Microsoft, dubbelklik op Inkoopassistent niet meer vanzelf starten, stop de "
           "assistent en verwijder deze map.")
# The start file: pythonw.exe, which has no console, started through "start", so that the file's own window ends at once.
START_LINE = '@start "" "%~dp0python\\pythonw.exe" -I -X utf8 -m aid.portable --start "%~dp0data"\r\n'
DEMO_START_LINE = '@start "" "%~dp0python\\pythonw.exe" -I -X utf8 -m aid.portable --demo "%~dp0demo"\r\n'
OLD_START_LINE = '@"%~dp0python\\python.exe" -I -X utf8 -m aid.portable --start "%~dp0data" || pause\r\n'   # of the earlier versions
STARTING = "De inkoopassistent start. Even geduld: de pagina opent vanzelf in uw browser."
STARTING_BOX = ("De inkoopassistent start. Even geduld: de pagina opent vanzelf in uw browser. De eerste keer kan dat een paar minuten "
                "duren.")                      # the information box of a start without a console
NOT_OPENED = "De pagina kon niet vanzelf worden geopend. Open zelf http://127.0.0.1:7860 in uw browser."
ALREADY_RUNNING = "De inkoopassistent draait al. De pagina wordt geopend."
NOT_STARTED = "De inkoopassistent kon niet starten. De reden staat in de map data, in logs, in het bestand start.log."
UNWRITABLE = ("De inkoopassistent kan in deze map niet schrijven. Zet de map op een plek waar u mag schrijven, bijvoorbeeld in uw eigen "
              "map Documenten, of vraag schrijfrechten op de map data.")
NOT_YET = "De pagina antwoordt nog niet. Open over een minuut zelf http://127.0.0.1:7860 in uw browser."
STOPPING = "De inkoopassistent stopt. Dit duurt hoogstens anderhalve minuut."
STOPPED = "De inkoopassistent is gestopt en blijft uit tot u weer op Inkoopassistent starten dubbelklikt."
STILL_STOPPING = "De inkoopassistent stopt nog. Wacht een minuut voordat u de map verplaatst of verwijdert."
NO_AUTOSTART = "De inkoopassistent start niet meer vanzelf wanneer u zich aanmeldt."
STARTING_UP = "Even geduld: de inkoopassistent wordt nog opgestart. De eerste keer kan dat een paar minuten duren."
REAL_DOWNLOAD = portable._download               # kept before the fixture below replaces it
# Rule 9 of the brief: the modules the self-check imports, the page library through the review page's import_gradio().
RULE_9 = ("aid.start", "aid.runner", "aid.capture_server", "aid.review.page", "gradio", "aid.madeup.generate", "aid.pricelist", "PIL")


@pytest.fixture(autouse=True)
def no_real_build(monkeypatch):
    """No test builds on the PC that runs the suite, a 64-bit Windows PC included: a build with its defaults sees no Windows, and its
    real download and its real pip fail the test. A test of the build gives the system, the machine, the fetch and the run itself."""
    monkeypatch.setattr(portable, "platform", types.SimpleNamespace(system=lambda: "Linux", machine=lambda: "x86_64"))

    def no_download(url: str) -> bytes:
        raise AssertionError(f"a test downloaded {url}")

    def no_pip(cmd, **kwargs):
        raise AssertionError(f"a test ran {cmd}")
    monkeypatch.setattr(portable, "_download", no_download)
    monkeypatch.setattr(portable, "_run", no_pip)


@pytest.fixture(autouse=True)
def no_real_box(monkeypatch):
    """No test shows a message box of Windows: the boxes that the start and the command take when they are given none (BOX) are
    replaced by ones that keep what reached them, and a test that reached them fails. They keep it rather than raise, since the
    information box is shown from a thread of its own, where an error would not reach the test."""
    reached = []

    class Reached:
        def __call__(self, text: str, warning: bool) -> None:
            reached.append(text)

        def close(self, thread: int) -> bool:
            return False
    monkeypatch.setattr(portable, "BOX", Reached())
    yield
    assert reached == [], f"a test reached the message boxes of Windows: {reached}"


@pytest.fixture
def windowless(monkeypatch):
    """A function that takes the console away, as under pythonw.exe, which the start file runs: sys.stdin, sys.stdout and sys.stderr
    are None. A test calls it first, since pytest puts its own capture into sys.stdout again when the test itself begins."""
    def no_console() -> None:
        for name in ("stdin", "stdout", "stderr", "__stdin__", "__stdout__", "__stderr__"):
            monkeypatch.setattr(sys, name, None)
    return no_console


# --- fakes ---

def embeddable_zip(version: tuple[int, int] = (3, 11)) -> bytes:
    """A small stand-in for python.org's embeddable package: the names it has, with made-up content, the same bytes each time."""
    tag = f"python{version[0]}{version[1]}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in (("python.exe", b"MZ made-up python"), ("pythonw.exe", b"MZ made-up pythonw"), (f"{tag}.dll", b"MZ made-up dll"),
                           (f"{tag}.zip", b"PK made-up library"), (f"{tag}._pth", ORIGINAL_PTH), ("LICENSE.txt", b"made-up licence")):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 11, 10, 9, 0, 0)), data)
    return buffer.getvalue()


class Fetch:
    """A fake download: the bytes of a small zip for each address asked; it keeps the addresses."""

    def __init__(self, data: bytes | None = None, error: Exception | None = None):
        self.data, self.error, self.asked = data if data is not None else embeddable_zip(), error, []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        if self.error is not None:
            raise self.error
        return self.data


class Pip:
    """A fake subprocess.run of pip: it keeps each command with the requirements file it was given, puts a package into the target
    (and, as pip does with --target, the console scripts into its folder bin, which hold the absolute path of the builder's Python),
    and ends with the exit code given."""

    def __init__(self, code: int = 0, package: str = "made_up_package"):
        self.code, self.package, self.calls = code, package, []

    def __call__(self, cmd, **kwargs):
        requirements = Path(cmd[cmd.index("-r") + 1]).read_text(encoding="utf-8")
        self.calls.append((cmd, kwargs, requirements))
        target = Path(cmd[cmd.index("--target") + 1])
        (target / self.package).mkdir(parents=True)
        (target / self.package / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
        (target / "bin").mkdir(exist_ok=True)
        (target / "bin" / "made-up-tool.exe").write_bytes(b"MZ #!" + sys.executable.encode() + b"\n")
        return subprocess.CompletedProcess(cmd, self.code, "", "")


def windows_build(out: Path, **more) -> int:
    """build on a Windows of the tests: the system and the machine as Windows on a 64-bit Intel or AMD PC gives them, a fake fetch
    and a fake pip unless the test gives its own."""
    more.setdefault("run", Pip())
    more.setdefault("fetch", Fetch())
    more.setdefault("version", VERSION)
    return portable.build(out, system="Windows", machine="AMD64", **more)


def files_of(folder: Path) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


class Launcher:
    """A fake subprocess.run of the launcher's commands (python -I -X utf8 -m aid.start --settings <file> <options>): the exit codes
    per options, in turn, the last one repeated; the output; an effect per options (to delete STOP, as --resume does); and every
    command in its order."""

    def __init__(self, codes: dict[tuple, int | list[int]], effects: dict | None = None, error: Exception | None = None):
        self.codes = {options: list(code) if isinstance(code, list) else [code] for options, code in codes.items()}
        self.effects, self.error, self.calls = effects or {}, error, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.error is not None:
            raise self.error
        options = tuple(cmd[cmd.index("--settings") + 2:])
        if options in self.effects:
            self.effects[options]()
        codes = self.codes[options]
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        return subprocess.CompletedProcess(cmd, code, f"made-up line of {' '.join(options)}\n", "")

    def options(self) -> list[tuple]:
        return [tuple(cmd[cmd.index("--settings") + 2:]) for cmd, _ in self.calls]


class Page:
    """A fake check of an address: its answers in turn, the last one repeated, whatever the address; it keeps the addresses asked."""

    def __init__(self, *answers: bool):
        self.answers, self.asked = list(answers), []

    def __call__(self, url: str) -> bool:
        self.asked.append(url)
        return self.answers[min(len(self.asked), len(self.answers)) - 1]


class Browser:
    """A fake of the browser: it keeps each address it opens, and, given the fake boxes, the information boxes that still stood then."""

    def __init__(self, works: bool = True, boxes: "Boxes | None" = None):
        self.works, self.boxes, self.opened, self.standing = works, boxes, [], []

    def __call__(self, url: str) -> bool:
        self.opened.append(url)
        if self.boxes is not None:
            self.standing.append(self.boxes.standing())
        return self.works


class Boxes:
    """A fake of the message boxes the start shows without a console (its parameter box): each box asked for is kept in its order
    in events, as ("information", text) or ("warning", text), with the thread that asked and whether the lock file stood then.
    An information box stands, as Windows' own does, until close is called with the native id of its thread, which keeps
    ("closed", text); after 5 seconds it keeps ("not closed", text) and ends, so that a start that never closes it fails its test
    and hangs none. A warning returns at once, as when the buyer clicks OK. delay holds an information box back before close can
    find it, as a box whose window Windows has yet to make."""

    def __init__(self, delay: float = 0.0, lock: Path | None = None):
        self.delay, self.lock_file, self.events, self.threads, self.lock_stood = delay, lock, [], [], []
        self.open: dict[int, tuple[str, threading.Event]] = {}
        self.guard = threading.Lock()

    def __call__(self, text: str, warning: bool) -> None:
        current = threading.current_thread()
        with self.guard:
            self.events.append(("warning" if warning else "information", text))
            self.threads.append((current.ident, current.daemon))
            self.lock_stood.append(self.lock_file is not None and self.lock_file.exists())
        if warning:
            return
        time.sleep(self.delay)
        closed = threading.Event()
        with self.guard:
            self.open[threading.get_native_id()] = (text, closed)
        if not closed.wait(5):
            with self.guard:
                self.open.pop(threading.get_native_id(), None)
                self.events.append(("not closed", text))

    def close(self, thread: int) -> bool:
        with self.guard:
            if thread not in self.open:
                return False
            text, closed = self.open.pop(thread)
            self.events.append(("closed", text))
        closed.set()
        return True

    def standing(self) -> list[str]:
        with self.guard:
            return [text for text, _ in self.open.values()]


class User32:
    """A fake of user32 for the message boxes of Windows (portable._MessageBoxes): MessageBoxW keeps its arguments; FindWindowExW
    walks the top-level windows given as (handle, class, title, thread) and matches a class and a title as Windows does, without
    regard to case; GetWindowThreadProcessId gives the thread that made a window; PostMessageW keeps what it posts."""

    def __init__(self, windows: list[tuple[int, str, str, int]] = ()):
        self.windows, self.shown, self.posted, self.asked = list(windows), [], [], []

    def MessageBoxW(self, owner, text, title, flags):
        self.shown.append((owner, text, title, flags))
        return 1

    def FindWindowExW(self, parent, after, kind, title):
        assert parent is None                                      # the top-level windows
        self.asked.append((kind, title))
        handles = [window[0] for window in self.windows]
        for handle, its_kind, its_title, _ in self.windows[handles.index(after) + 1 if after else 0:]:
            if its_kind.lower() == kind.lower() and its_title.lower() == title.lower():
                return handle
        return None

    def GetWindowThreadProcessId(self, window, process):
        assert process is None
        return next(thread for handle, _, _, thread in self.windows if handle == window)

    def PostMessageW(self, window, message, wparam, lparam):
        self.posted.append((window, message, wparam, lparam))
        return 1


@pytest.fixture
def quick(monkeypatch):
    """The self-check passes, the page is asked every hundredth of a second instead of every second, and the start gives a launcher
    a twentieth of a second to run."""
    monkeypatch.setattr(portable, "self_check", lambda: [])
    monkeypatch.setattr(portable, "WAIT_STEP", 0.01)
    monkeypatch.setattr(portable, "LAUNCHER_WAIT", 0.05)


def command(data: Path, *options: str) -> list[str]:
    return [sys.executable, "-I", "-X", "utf8", "-m", "aid.start", "--settings", str(data.resolve() / "settings.json"), *options]


def log(data: Path) -> list[str]:
    """The lines of data/logs/start.log, without their time."""
    return [line[20:] for line in (data / "logs" / "start.log").read_text(encoding="utf-8").splitlines()]


def said(capsys) -> list[str]:
    return capsys.readouterr().out.splitlines()


# --- the folder's files ---

def test_the_pth_file_names_the_library_the_site_folder_and_the_app():
    """Five lines, as Windows writes them: the library zip, the folder itself, Lib\\site-packages, the folder app beside the
    folder python, and import site."""
    assert portable.pth_text((3, 11)) == "python311.zip\r\n.\r\nLib\\site-packages\r\n..\\app\r\nimport site\r\n"
    assert portable.pth_text((3, 13)).splitlines() == ["python313.zip", ".", "Lib\\site-packages", "..\\app", "import site"]
    assert portable.embed_url((3, 11, 9)) == URL
    assert portable.embed_url((3, 14, 10)) == "https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip"


def test_the_start_files_call_the_python_of_their_own_folder_and_hold_no_absolute_path():
    files = portable.start_files()
    assert sorted(files) == sorted([*CMD_FILES, *DEMO_FILES, "LEESMIJ.txt"])
    for name, option in CMD_FILES.items():
        text = files[name]
        line = text.removesuffix("\r\n")
        assert "\n" not in line and "\r" not in line and text.isascii(), name                        # one line
        # -I: the user's own site-packages and Python's environment variables never reach the folder's Python
        assert f' -I -X utf8 -m aid.portable {option} "%~dp0data"' in line, name
        assert not re.search(r"[A-Za-z]:[\\/]|\\\\|(^|\s)/", line), name                              # no drive, no network path
    # the start: the folder's pythonw.exe, which has no console, through "start", so that the file's window ends at once; no pause
    assert files["Inkoopassistent starten.cmd"] == START_LINE and "pause" not in START_LINE and "python.exe" not in START_LINE
    # the stop and the end of the start at logon keep their window, with python.exe, and wait for a key
    for name in ("Inkoopassistent stoppen.cmd", "Inkoopassistent niet meer vanzelf starten.cmd"):
        assert files[name].startswith(f'@"%~dp0python\\python.exe" -I -X utf8 -m aid.portable {CMD_FILES[name]} "%~dp0data"'), name
        assert files[name].endswith(" & pause\r\n"), name
    # the demonstration: the same Pythons and windows, on the folder demo beside data
    assert files["Demonstratie starten.cmd"] == DEMO_START_LINE and "data" not in DEMO_START_LINE
    assert files["Demonstratie stoppen.cmd"] == '@"%~dp0python\\python.exe" -I -X utf8 -m aid.portable --demo-stop "%~dp0demo" & pause\r\n'
    readme = files["LEESMIJ.txt"]
    assert " ".join(readme.split()) == LEESMIJ and readme.isascii() and "\r\n" in readme and "\n" not in readme.replace("\r\n", "")


def test_the_manual_recommends_a_short_path_for_the_windows_folder():
    manual = (REPO_ROOT / "aid" / "MANUAL.md").read_text(encoding="utf-8")
    carry = manual.split("**Carry.**", 1)[1].split("**Start.**", 1)[0]
    assert r"Use a short path such as `C:\Inkoopassistent`" in carry
    assert "Windows can refuse paths longer than 260 characters" in carry


def test_leesmij_says_how_the_buyer_unblocks_starts_and_updates_the_folder():
    """LEESMIJ.txt: a zip from the internet is unblocked before it is unpacked; one unpacked already is taken away and unpacked
    anew, never a folder data with it, as a file unpacked over one that is there keeps its block when it is skipped. The start leaves
    no window open and shows a message instead; after a logoff or a restart during a start, its lock keeps a double-click from
    doing anything for up to seven and a half minutes. An update replaces every file of the folder but the folder data, so that the
    new start files come along, with what is inside the zip's folder Inkoopassistent, not that folder itself, whose start file would
    start a level down with a new, empty data folder."""
    readme = " ".join(portable.start_files()["LEESMIJ.txt"].split())
    unblock = "klik met de rechtermuisknop op het zip-bestand, kies Eigenschappen, vink Blokkering opheffen aan en klik op OK"
    assert unblock in readme and readme.index("voordat u het uitpakt") < readme.index(unblock)
    assert ("Hebt u het al uitgepakt, stop dan de assistent als die draait en verwijder wat u uitpakte, maar nooit een map data; hef "
            "daarna de blokkering alsnog op en pak het zip-bestand opnieuw uit.") in readme and "op dezelfde plek" not in readme
    assert "Er blijft geen venster open: een melding zegt dat de assistent start" in readme
    assert "Dubbelklikt u nog eens terwijl de assistent start, dan gebeurt er niets: de eerste start opent de pagina." in readme
    assert "dan doet een dubbelklik tot acht minuten na de eerste dubbelklik niets" in readme
    assert portable.LAUNCHER_WAIT + 180 + portable.LOCK_MARGIN < 8 * 60
    assert "verwijder in deze map alles behalve de map data, zet alles wat in de map van de nieuwe versie staat in deze map" in readme
    assert ("Het zip-bestand van een nieuwe versie bevat een map Inkoopassistent: pak het eerst uit op een andere plek, bijvoorbeeld in "
            "Downloads, en zet wat in die map Inkoopassistent staat in deze map, niet die map zelf.") in readme
    assert "ook de bestanden waarop u dubbelklikt" in readme and "vervang de mappen python en app" not in readme
    assert "zet alles uit de nieuwe versie in deze map" not in readme


@pytest.mark.skipif(os.name == "nt", reason="a copy of the Python of Windows needs its DLLs beside it; the run on Linux shows the rule")
def test_the_folders_python_with_its_pth_reads_the_users_own_packages_unless_it_runs_with_I(tmp_path):
    """A copy of this Python beside a ._pth like the build's (the library, the folder itself, Lib/site-packages, the folder app,
    import site) still imports a module that exists only in the user's own site-packages, and honours PYTHONIOENCODING; with -I
    neither. So every Python of the folder is started with -I. On Linux CPython looks for <executable>._pth."""
    python = tmp_path / "folder" / "python"
    (python / "Lib" / "site-packages").mkdir(parents=True)
    (tmp_path / "folder" / "app").mkdir()
    copy = python / "python"
    shutil.copy2(os.path.realpath(sys.executable), copy)
    library = [sysconfig.get_path("stdlib"), os.path.join(sysconfig.get_path("platstdlib"), "lib-dynload")]
    (python / "python._pth").write_text("\n".join([*library, ".", "Lib/site-packages", "../app", "import site"]) + "\n", encoding="utf-8")
    home = tmp_path / "home"
    user_site = home / ".local" / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages"
    user_site.mkdir(parents=True)
    (user_site / "only_in_the_user_site.py").write_text("WHERE = 'the user site'\n", encoding="utf-8")
    probe = ("import sys\ntry:\n    import only_in_the_user_site\n    found = 'imported'\nexcept ImportError:\n    found = 'not found'\n"
             "print(found + '|' + sys.stdout.encoding)\n")
    environment = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "latin-1"}
    seen = {}
    for options in ((), ("-I",)):
        done = subprocess.run([str(copy), *options, "-X", "utf8", "-c", probe], capture_output=True, text=True, env=environment, timeout=120)
        assert done.returncode == 0, done.stderr
        seen[options] = done.stdout.strip().split("|")
    assert seen[()] == ["imported", "iso8859-1"]                 # the ._pth alone keeps the user site and the variables
    assert seen[("-I",)] == ["not found", "utf-8"]


def test_the_requirements_leave_the_development_clients_library_out():
    text = (REPO_ROOT / "aid" / "requirements.txt").read_text(encoding="utf-8")
    kept = portable.requirements(text)
    assert [line for line in text.splitlines() if not line.startswith("anthropic")] == kept.splitlines()
    assert "anthropic" not in kept and "gradio==" in kept and "msal-extensions==" in kept and "pillow==" in kept
    assert text.splitlines()[0].endswith("Python 3.11 or newer.")       # the owner's PC runs 3.11, and the README says so
    for line in ("Anthropic == 2.3.0", "anthropic>=2", "anthropic[vertex]==2.3.0 ; python_version >= '3.11'", "  anthropic  "):
        assert portable.requirements(f"# a comment\npydantic==2.13.5\n{line}\n") == "# a comment\npydantic==2.13.5\n", line
    assert portable.requirements("anthropic-tools==1.0\n") == "anthropic-tools==1.0\n"           # another package


def test_the_sha256_values_are_python_orgs_with_the_page_they_come_from():
    """Each value of EMBED_SHA256 stands with the release page it was read from (none from memory)."""
    source = Path(portable.__file__).read_text(encoding="utf-8")
    assert portable.EMBED_SHA256
    for version, value in portable.EMBED_SHA256.items():
        assert re.fullmatch(r"3\.\d+\.\d+", version) and re.fullmatch(r"[0-9a-f]{64}", value), version
        [line] = [line for line in source.splitlines() if value in line]
        assert f"https://www.python.org/downloads/release/python-{version.replace('.', '')}/" in line, version


# --- the build ---

def test_build_makes_the_folder_and_a_second_build_keeps_data(tmp_path, capsys):
    out = tmp_path / "Inkoopassistent"
    fetch, pip = Fetch(), Pip()
    assert windows_build(out, run=pip, fetch=fetch) == 0
    assert fetch.asked == [URL]
    assert sorted(path.name for path in out.iterdir()) == FOLDER                       # no data folder: the first start makes it
    for name, text in portable.start_files().items():
        assert (out / name).read_bytes() == text.encode("ascii")
    python = out / "python"
    assert (python / "python.exe").is_file() and (python / "pythonw.exe").is_file() and (python / "python311.zip").is_file()
    assert (python / "python311._pth").read_bytes() == portable.pth_text((3, 11)).encode("ascii")
    site = python / "Lib" / "site-packages"
    assert (site / "made_up_package" / "__init__.py").is_file() and not (site / "bin").exists()   # pip's scripts hold the builder's path
    # pip once, with this Python, into the folder's site-packages, with the requirements without the development client's library
    [(cmd, kwargs, requirements)] = pip.calls
    assert cmd[:4] == [sys.executable, "-m", "pip", "install"]
    assert Path(cmd[cmd.index("--target") + 1]).parts[-3:] == ("python", "Lib", "site-packages") and "--no-compile" in cmd
    assert requirements == portable.requirements((REPO_ROOT / "aid" / "requirements.txt").read_text(encoding="utf-8"))
    # the package without its tests and its cache folders
    app = out / "app" / "aid"
    assert (app / "portable.py").is_file() and (app / "start.py").is_file() and (app / "settings.example.json").is_file()
    assert (app / "madeup" / "out").is_dir() and (app / "extension" / "manifest.json").is_file() and (app / "requirements.txt").is_file()
    assert not (app / "tests").exists() and not [path for path in (out / "app").rglob("*") if path.name in ("__pycache__", "settings.json")]
    # no file holds an absolute path of the build: the folder may be carried to another PC
    for name, data in files_of(out).items():
        for path in (str(tmp_path), str(REPO_ROOT), sys.executable):
            assert path.encode() not in data, (name, path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Inkoopassistent"]       # nothing else left behind
    lines = said(capsys)
    sha256 = hashlib.sha256(Fetch().data).hexdigest()
    assert any(sha256 in line and "https://www.python.org/downloads/release/python-3119/" in line for line in lines), lines
    # a second build replaces python and app and leaves data as it is
    (out / "data" / "cases" / "250101_20261110T090000").mkdir(parents=True)
    (out / "data" / "settings.json").write_text('{"made": "up"}', encoding="utf-8")
    (out / "data" / "STOP").write_text("", encoding="utf-8")
    (out / "demo" / "cases").mkdir(parents=True)                                         # a demonstration was started
    (out / "demo" / "DEMONSTRATIE.txt").write_text(portable.DEMO_MARKER_TEXT, encoding="utf-8")
    (out / "app" / "aid" / "left_from_the_first_build.py").write_text("", encoding="utf-8")
    (out / "Inkoopassistent starten.cmd").write_bytes(OLD_START_LINE.encode("ascii"))       # the start file of an earlier version
    data_before = files_of(out / "data")
    assert windows_build(out, run=Pip(package="other_package")) == 0
    assert files_of(out / "data") == data_before and (out / "data" / "cases" / "250101_20261110T090000").is_dir()
    for name, text in portable.start_files().items():                                       # every start file written anew
        assert (out / name).read_bytes() == text.encode("ascii"), name
    assert (out / "Inkoopassistent starten.cmd").read_bytes() == START_LINE.encode("ascii")
    assert (site / "other_package").is_dir() and not (site / "made_up_package").exists()
    assert not (out / "app" / "aid" / "left_from_the_first_build.py").exists() and (out / "app" / "aid" / "portable.py").is_file()
    assert sorted(path.name for path in out.iterdir()) == sorted([*FOLDER, "data", "demo"]) and (out / "demo" / "cases").is_dir()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Inkoopassistent"]


def test_build_can_take_the_zip_from_a_file(tmp_path):
    given = tmp_path / "given" / "python-3.11.9-embed-amd64.zip"
    given.parent.mkdir()
    given.write_bytes(embeddable_zip())
    fetch = Fetch(error=AssertionError("no download when a file is given"))
    assert windows_build(tmp_path / "out", python_zip=given, fetch=fetch) == 0
    assert fetch.asked == [] and (tmp_path / "out" / "python" / "python.exe").is_file()


def test_build_refuses_another_system_and_a_folder_with_other_content(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    fetch, pip = Fetch(), Pip()
    for system, machine in (("Linux", "x86_64"), ("Darwin", "arm64"), ("Windows", "ARM64"), ("Windows", "x86")):
        assert portable.build(out, run=pip, fetch=fetch, system=system, machine=machine, version=VERSION) == 2
        [line] = said(capsys)
        assert "Windows" in line and "64-bit" in line and "--build" in line, line            # one line: where to run it
    assert portable.main(["--build", str(out)]) == 2                                         # the defaults: no Windows (no_real_build)
    assert len(said(capsys)) == 1
    with monkeypatch.context() as patched:                                                   # a 32-bit Python on a 64-bit Windows
        patched.setattr(portable.sys, "maxsize", 2 ** 31 - 1)
        refused = portable.build(out, run=pip, fetch=fetch, system="Windows", machine="AMD64", version=VERSION)
    assert refused == 2 and "64-bit Python" in said(capsys)[0]
    assert fetch.asked == [] and pip.calls == [] and not out.exists()
    # an out folder that holds anything but an earlier build
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    (out / "data").mkdir()
    assert windows_build(out, run=pip, fetch=fetch) == 2
    assert "notes.txt" in "\n".join(said(capsys))
    assert sorted(path.name for path in out.iterdir()) == ["data", "notes.txt"] and fetch.asked == [] and pip.calls == []
    (out / "notes.txt").rename(out / "python.txt")
    assert windows_build(out, run=pip, fetch=fetch) == 2
    file_out = tmp_path / "a file"
    file_out.write_text("", encoding="utf-8")
    assert windows_build(file_out, run=pip, fetch=fetch) == 2
    # an out folder inside the package itself, which the build copies
    package = tmp_path / "package" / "aid"
    package.mkdir(parents=True)
    monkeypatch.setattr(portable, "PACKAGE", package)
    assert windows_build(package / "out", run=pip, fetch=fetch) == 2 and not (package / "out").exists()
    assert fetch.asked == [] and pip.calls == []


def test_build_reads_the_system_when_it_is_called(tmp_path, monkeypatch):
    """The defaults of the system, the machine and the version are read when build is called, never when the module is imported,
    so that a test can make the PC look like Windows, or like no Windows."""
    for name in ("system", "machine", "version", "run", "fetch"):
        assert inspect.signature(portable.build).parameters[name].default is None, name
    monkeypatch.setattr(portable, "platform", types.SimpleNamespace(system=lambda: "Windows", machine=lambda: "AMD64"))
    data = embeddable_zip(sys.version_info[:2])
    # The build checks the package of a version that EMBED_SHA256 names against the sha256 there. The made-up package gets its own
    # sha256 under the version of this Python, so that the test holds on every Python, also on one the table names (3.13.17).
    monkeypatch.setitem(portable.EMBED_SHA256, ".".join(str(part) for part in sys.version_info[:3]), hashlib.sha256(data).hexdigest())
    fetch = Fetch(data=data)
    assert portable.build(tmp_path / "out", run=Pip(), fetch=fetch) == 0
    assert fetch.asked == [portable.embed_url(sys.version_info[:3])]


def test_build_stops_on_a_wrong_sha256_and_on_a_failing_pip(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    pip = Pip()
    # a download that fails, and a file that is no zip: nothing is built and nothing is left behind
    assert windows_build(out, run=pip, fetch=Fetch(error=OSError("made-up: no connection"))) == 1
    assert windows_build(out, run=pip, fetch=Fetch(data=b"no zip")) == 1
    assert pip.calls == [] and list(tmp_path.iterdir()) == []
    # a version of EMBED_SHA256 whose sha256 does not match
    good = hashlib.sha256(embeddable_zip()).hexdigest()
    monkeypatch.setitem(portable.EMBED_SHA256, "3.11.9", "0" * 64)
    capsys.readouterr()
    assert windows_build(out, run=pip) == 1
    assert pip.calls == [] and list(tmp_path.iterdir()) == []
    text = "\n".join(said(capsys))
    assert good in text and "0" * 64 in text
    # the value python.org gives: the build goes on, and says so
    monkeypatch.setitem(portable.EMBED_SHA256, "3.11.9", good)
    assert windows_build(out) == 0
    assert any("matches" in line for line in said(capsys))
    # a failing pip ends the build with its exit code, and a second build that fails leaves the first one as it was
    before = files_of(out)
    assert windows_build(out, run=Pip(code=7, package="other_package")) == 7
    assert files_of(out) == before and sorted(path.name for path in tmp_path.iterdir()) == ["out"]
    assert windows_build(tmp_path / "new", run=Pip(code=1)) == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out"]


def test_a_version_python_org_has_no_windows_package_for_says_which_to_use(tmp_path, capsys, monkeypatch):
    """python.org gives no Windows files for the releases after 3.11.9 and after 3.12.10: a 404 says so, and names what builds. The
    embeddable package of such a version, given with --python-zip under the name python.org gives it, is checked and named by the
    version in its name, not by the version of the Python that runs the build."""
    missing = urllib.error.HTTPError("https://www.python.org/ftp/python/3.11.14/python-3.11.14-embed-amd64.zip", 404, "Not Found", None, None)
    assert windows_build(tmp_path / "out", fetch=Fetch(error=missing), version=(3, 11, 14)) == 1
    [_, line] = said(capsys)
    assert line == ("python.org has no Windows package for Python 3.11.14: build the folder with a Python of python.org of a version "
                    "that has one (for 3.11 the last is 3.11.9), or with --python-zip and the embeddable package of such a version of "
                    "Python 3.11 (python-3.11.9-embed-amd64.zip, from https://www.python.org/downloads/release/python-3119/): nothing was built")
    missing = urllib.error.HTTPError("https://www.python.org/ftp/python/3.14.99/python-3.14.99-embed-amd64.zip", 404, "Not Found", None, None)
    assert windows_build(tmp_path / "out", fetch=Fetch(error=missing), version=(3, 14, 99)) == 1
    assert said(capsys)[1] == ("python.org has no Windows package for Python 3.14.99: build the folder with a Python of python.org of a "
                               "version that has one, or with --python-zip and the embeddable package of such a version of Python 3.14: "
                               "nothing was built")
    assert not (tmp_path / "out").exists()
    given = tmp_path / "given" / "python-3.11.9-embed-amd64.zip"
    given.parent.mkdir()
    given.write_bytes(embeddable_zip())
    assert windows_build(tmp_path / "out", python_zip=given, fetch=Fetch(error=AssertionError("no download")), version=(3, 11, 14)) == 0
    sha256, md5 = hashlib.sha256(given.read_bytes()).hexdigest(), hashlib.md5(given.read_bytes()).hexdigest()
    assert (f"compare the sha256 {sha256} and the md5 {md5} of python-3.11.9-embed-amd64.zip with its release page "
            "https://www.python.org/downloads/release/python-3119/") in "\n".join(said(capsys))
    assert (tmp_path / "out" / "python" / "python311._pth").is_file()
    monkeypatch.setitem(portable.EMBED_SHA256, "3.11.9", "0" * 64)          # a version of EMBED_SHA256: the file's version decides
    assert windows_build(tmp_path / "other", python_zip=given, version=(3, 11, 14)) == 1
    assert "0" * 64 in "\n".join(said(capsys)) and not (tmp_path / "other").exists()


def test_a_failed_second_build_puts_the_earlier_folder_back(tmp_path, monkeypatch, capsys):
    """Windows keeps a folder in use while a file in it is open: when app cannot be moved aside after python was, python goes back,
    and the earlier build and data stay as they were."""
    out = tmp_path / "Inkoopassistent"
    assert windows_build(out) == 0
    (out / "data").mkdir()
    (out / "data" / "settings.json").write_text('{"made": "up"}', encoding="utf-8")
    before = files_of(out)
    replace = os.replace

    def in_use(source, target):
        if Path(source) == out / "app":
            raise PermissionError(13, "made-up: a file of app is held open", str(source))
        return replace(source, target)
    monkeypatch.setattr(portable.os, "replace", in_use)
    capsys.readouterr()
    assert windows_build(out, run=Pip(package="other_package")) == 1
    assert files_of(out) == before and (out / "python" / "python.exe").is_file()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Inkoopassistent"]
    assert said(capsys)[-1].startswith(f"python and app could not be replaced in {out} (PermissionError")


def test_a_failed_build_into_a_new_folder_removes_the_folders_it_made(tmp_path):
    out = tmp_path / "new" / "nested" / "Inkoopassistent"
    assert windows_build(out, run=Pip(code=3)) == 3
    assert list(tmp_path.iterdir()) == []                              # neither new, nor nested, nor the staging folder
    assert windows_build(out) == 0 and (out / "python" / "python.exe").is_file()


def test_the_build_copies_the_program_only_and_names_what_it_leaves_out(tmp_path, monkeypatch, capsys):
    """What a checkout may hold beside the program: the tests, cache folders, a run's folders and files, evaluation results and a
    settings file under another name. Without git (or outside a git repository) the build copies an allow-list: the package's .py
    files and the files the code reads; in a git repository the files git tracks. Either way every other file is named, and only
    the tests, the cache folders and hidden files are left out without a word."""
    program = ["start.py", "portable.py", "review/page.py", "requirements.txt", "settings.example.json", "README.md", "MANUAL.md",
               "madeup/out/MU01_conf.pdf", "madeup/spec/MU01.json", "madeup/tryout/EXPORT_nl.csv", "extension/manifest.json", "readers/templates/table_nl.json",
               "readers/prompts/decide.md", "messaging/templates/nl/question.txt"]
    silent = ["tests/test_start.py", "tests/fixtures/recorded/x.json", "__pycache__/start.cpython-311.pyc", "review/__pycache__/page.pyc",
              ".pytest_cache/v/x", "colab/.ipynb_checkpoints/x.ipynb", "review/old.pyc"]
    named = ["evalscripts/results/necessity_2026-11-10.csv", "evalscripts/results/recorded/model-x/0a1b.json", "live_settings.json",
             "inbox/MU03_conf.pdf", "cases/250101_20261110T090000/case.json", "settings.json", "STOP", "logs/start.log"]
    package = tmp_path / "checkout" / "aid"
    for name in program + silent + named:
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_bytes((REPO_ROOT / "aid" / "requirements.txt").read_bytes() if name == "requirements.txt" else b"x")
    monkeypatch.setattr(portable, "PACKAGE", package)
    tracked = portable._tracked
    assert tracked(package) is None                                                    # no git repository holds it
    monkeypatch.setattr(portable, "_tracked", lambda folder: None)                     # no git, or no repository
    assert windows_build(tmp_path / "out") == 0
    assert sorted(files_of(tmp_path / "out" / "app" / "aid")) == sorted(program)
    [line] = [line for line in said(capsys) if line.startswith("Left out of app")]
    assert line == f"Left out of app/aid, as no part of the program: {', '.join(sorted(named))}"
    # in a git repository: the files git tracks, the others named
    if shutil.which("git") is None:
        return
    monkeypatch.setattr(portable, "_tracked", tracked)
    git = ["git", "-C", str(package.parent), "-c", "user.name=made-up", "-c", "user.email=made-up@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", *[f"aid/{name}" for name in program + silent[:2]]], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "made-up"], check=True)
    assert sorted(portable._tracked(package)) == sorted(program + silent[:2])
    assert windows_build(tmp_path / "out git") == 0
    assert sorted(files_of(tmp_path / "out git" / "app" / "aid")) == sorted(program)
    [line] = [line for line in said(capsys) if line.startswith("Left out of app")]
    assert line == f"Left out of app/aid, as no part of the program: {', '.join(sorted(named))}"


def test_the_download_takes_https_addresses_of_python_org_only():
    for url in ("http://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip", "https://example.org/python-3.11.9-embed-amd64.zip",
                "https://www.python.org.example.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"):
        with pytest.raises(ValueError, match="no address of https://www.python.org/"):
            REAL_DOWNLOAD(url)


# --- the first start ---

def test_first_start_writes_loadable_settings_once(tmp_path):
    data = tmp_path / "Inkoopassistent" / "data"
    path = portable.first_start(data)
    assert path == data / "settings.json"
    settings = load_settings(path)
    assert settings.reader == "parser"
    assert (settings.cases_root, settings.po_store_root, settings.watched_folder) == (data / "cases", data / "po_store", data / "inbox")
    assert all((data / name).is_dir() for name in ("inbox", "cases", "po_store", "logs"))
    token = settings.capture_token
    assert len(token) >= 32 and token != load_settings(portable.first_start(tmp_path / "other")).capture_token     # random each time
    addresses = [settings.buyer_email, settings.aid_email, settings.approver_email, *settings.supplier_emails.values()]
    assert addresses and all(address.endswith("@example.com") for address in addresses)
    assert json.loads(path.read_text(encoding="utf-8"))["cases_root"] == "cases"             # beside it: the folder may be moved
    # never overwritten: a second start, or a file the buyer's settings tab changed
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    assert portable.first_start(data, token=lambda: "another-made-up-token-of-forty-characters") == path
    assert path.read_bytes() == before and path.stat().st_mtime_ns == modified
    path.write_text("{ not json", encoding="utf-8")
    portable.first_start(data)
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_the_first_settings_hold_the_test_mode_on(tmp_path):
    """Task 33: the first settings file of the folder version has the test mode on, for the owner's test with the test Exact; the
    example keeps it off, and the hand-over switches it off (MANUAL 3.16 and 3.17). The key stands where the example has it."""
    path = portable.first_start(tmp_path / "data")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["test_mode"] is True and load_settings(path).test_mode is True
    example = json.loads((portable.PACKAGE / "settings.example.json").read_text(encoding="utf-8"))
    assert example["test_mode"] is False and list(written) == list(example)


# --- the start, the stop and the start at logon ---

def test_start_sets_the_start_at_logon_waits_and_opens_the_page(tmp_path, capsys, quick):
    data = tmp_path / "Inkoopassistent" / "data"
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0})
    page, browser = Page(False, False, False, True), Browser()
    assert portable.start(data, run=launcher, answers=page, open_page=browser, wait_seconds=30) == 0
    assert (data / "settings.json").is_file()                                                 # the first start
    assert [cmd for cmd, _ in launcher.calls] == [command(data, "--status"), command(data, "--install-autostart"), command(data, "--status")]
    assert all(kwargs["stdin"] == subprocess.DEVNULL and kwargs["capture_output"] for _, kwargs in launcher.calls)
    assert page.asked == [PAGE, CAPTURE, PAGE, PAGE] and browser.opened == [PAGE]            # both addresses first, then the page
    assert said(capsys) == [STARTING]
    assert log(data) == ["folder start: --status ended with exit code 1: made-up line of --status",
                         "folder start: --install-autostart ended with exit code 0: made-up line of --install-autostart",
                         "folder start: --status ended with exit code 0: made-up line of --status",
                         "folder start: the page answers and was opened in the browser"]
    # a launcher already runs: only the page is opened
    launcher = Launcher({("--status",): 0})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=browser, wait_seconds=30) == 0
    assert launcher.options() == [("--status",)] and browser.opened == [PAGE, PAGE]
    assert said(capsys) == [ALREADY_RUNNING]
    assert log(data)[-3:] == ["folder start: --status ended with exit code 0: made-up line of --status",
                              "folder start: a launcher runs on these settings: only the page is opened",
                              "folder start: the page answers and was opened in the browser"]
    # the page does not answer in time
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0})
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=0.05) == 1
    assert said(capsys) == [STARTING, NOT_YET] and browser.opened == [PAGE, PAGE]
    assert log(data)[-1] == "folder start: the page did not answer within 0.05 seconds"
    assert inspect.signature(portable.start).parameters["wait_seconds"].default == 180


def test_start_resumes_after_a_stop(tmp_path, capsys, quick):
    data = tmp_path / "data"
    portable.first_start(data)
    (data / "STOP").write_text("", encoding="utf-8")                                          # the buyer's stop
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0, ("--resume", "--background"): 0},
                        effects={("--resume", "--background"): (data / "STOP").unlink})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30) == 0
    assert launcher.options() == [("--status",), ("--install-autostart",), ("--resume", "--background"), ("--status",)]
    assert said(capsys) == [STARTING]
    # a launcher that is still stopping is not one that runs: the start waits for it through --resume
    (data / "STOP").write_text("", encoding="utf-8")
    launcher = Launcher({("--status",): 0, ("--install-autostart",): 0, ("--resume", "--background"): 0},
                        effects={("--resume", "--background"): (data / "STOP").unlink})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=Browser(), wait_seconds=30) == 0
    assert launcher.options() == [("--status",), ("--install-autostart",), ("--resume", "--background"), ("--status",)]
    # without STOP no --resume
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30) == 0
    assert launcher.options() == [("--status",), ("--install-autostart",), ("--status",)]


def test_the_start_checks_both_addresses_and_that_a_launcher_runs(tmp_path, capsys, quick):
    """The launcher starts nothing while one of its two addresses answers, and --install-autostart still ends with 0 once the start
    at logon is set: so the start asks both addresses first, and --status after the launcher's start, before it waits for the page."""
    data = tmp_path / "data"
    # a capture server started by hand answers: nothing is started
    launcher = Launcher({("--status",): 1})
    assert portable.start(data, run=launcher, answers=lambda url: url == CAPTURE, open_page=Browser(), wait_seconds=30) == 1
    assert launcher.options() == [("--status",)] and said(capsys) == [STARTING, NOT_STARTED]
    assert log(data)[-1] == (f"folder start: something already answers at {CAPTURE} while no launcher runs on these settings (another "
                             "aid on this PC, or a part started by hand): only one aid runs on a PC; stop the other one and start "
                             "again; nothing started")
    # the start at logon is set, and still no launcher runs: the window says that it waits, then that the start failed
    launcher = Launcher({("--status",): 1, ("--install-autostart",): 0})
    browser = Browser()
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=30) == 1
    assert launcher.options()[:3] == [("--status",), ("--install-autostart",), ("--status",)] and browser.opened == []
    assert said(capsys) == [STARTING, STARTING_UP, NOT_STARTED]
    assert log(data)[-2:] == ["folder start: --status ended with exit code 1: made-up line of --status",
                              "folder start: no launcher runs on these settings 0.05 seconds after its start; the launcher's lines "
                              "above say why; nothing to wait for"]
    # a launcher that comes up late: the window says once that it waits, and the page opens
    launcher = Launcher({("--status",): [1, 1, 1, 0], ("--install-autostart",): 0})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=browser, wait_seconds=30) == 0
    assert said(capsys) == [STARTING, STARTING_UP] and browser.opened == [PAGE]
    assert log(data)[-2] == "folder start: --status ended with exit code 0: made-up line of --status"     # the last ask only


def test_the_start_gives_a_slow_launcher_a_minute_and_a_half():
    """A first start on a slow PC (a virus scanner that checks the new files) is not reported as a failure while the launcher still
    comes up: the start asks --status for up to 90 seconds, and then waits up to 180 seconds for the page. Its start lock counts as
    left over after those waits and three minutes more, seven and a half minutes in all."""
    assert portable.LAUNCHER_WAIT == 90 and inspect.signature(portable.start).parameters["wait_seconds"].default == 180
    assert portable.LOCK_MARGIN == 180 and portable.LAUNCHER_WAIT + 180 + portable.LOCK_MARGIN == 7.5 * 60


def test_the_start_asks_its_local_addresses_without_any_proxy(monkeypatch):
    """A proxy of the PC cannot reach this PC's own 127.0.0.1: the start asks the launcher's two addresses and the page without any
    proxy, also when the environment names one, so that a page that answers is seen as answering."""
    import urllib.request

    from aid.tests.helpers import local_page, unreachable_proxy, without_proxies
    without_proxies(monkeypatch)
    monkeypatch.setenv("http_proxy", unreachable_proxy())
    with local_page() as url:
        with pytest.raises(urllib.error.URLError):                     # through the proxy the page cannot be reached
            urllib.request.urlopen(url, timeout=5)
        assert portable._answers(url) is True
    assert inspect.signature(portable.start).parameters["answers"].default is portable._answers


def test_a_failed_self_check_starts_nothing_and_logs_why(tmp_path, capsys, monkeypatch):
    """Each module that cannot be imported gets one line with the class of the error and the first line of its message: a missing
    package (rebuild the folder) reads otherwise than a DLL that Windows blocks (ask IT)."""
    data = tmp_path / "data"
    real = portable.importlib.import_module

    def blocked(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("DLL load failed while importing _imaging: made-up reason of the policy\nmade-up second line")
        return real(name, *args, **kwargs)
    monkeypatch.setattr(portable.importlib, "import_module", blocked)
    launcher, browser = Launcher({("--status",): 1}), Browser()
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=30) == 1
    assert launcher.options() == [("--status",)] and browser.opened == []                   # nothing started
    assert said(capsys) == [STARTING, NOT_STARTED]
    assert log(data)[-2:] == ["folder start: PIL cannot be imported (ImportError: DLL load failed while importing _imaging: made-up reason "
                              "of the policy)", "folder start: these modules cannot be imported: PIL; nothing started"]


def test_a_failed_self_check_starts_nothing_and_is_logged(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(portable, "self_check", lambda: ["aid.review.page", "gradio"])
    launcher, browser = Launcher({("--status",): 1}), Browser()
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=30) == 1
    assert launcher.options() == [("--status",)] and browser.opened == []                   # nothing started
    assert said(capsys) == [STARTING, NOT_STARTED]
    assert log(data)[-1] == "folder start: these modules cannot be imported: aid.review.page, gradio; nothing started"


def test_the_self_check_names_each_module_that_cannot_be_imported(monkeypatch):
    assert portable.SELF_CHECK == RULE_9
    assert portable.self_check() == []                                                       # this Python has every package
    monkeypatch.setitem(sys.modules, "PIL", None)                                            # as when the package is missing
    assert portable.self_check() == ["PIL"] and portable.self_check()[0].reason.startswith("ModuleNotFoundError: ")


SELF_CHECK_EACH = """
import importlib.abc, json, sys
import aid.review.page
from aid import portable

class Refusing(importlib.abc.MetaPathFinder):
    def __init__(self, name):
        self.name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name:
            raise RuntimeError("made-up error of the import")
        return None

assert portable.self_check() == [], portable.self_check()
found = {}
for name in portable.SELF_CHECK:
    found[name] = []
    for way in ("missing", "another error"):
        kept = sys.modules.get(name)
        if name == "gradio" and way == "another error":
            real = aid.review.page.import_gradio
            aid.review.page.import_gradio = lambda: (_ for _ in ()).throw(RuntimeError("made-up error of the import"))
            found[name].append(portable.self_check())
            aid.review.page.import_gradio = real
            continue
        if way == "missing":
            sys.modules[name] = None
        else:
            del sys.modules[name]
            sys.meta_path.insert(0, Refusing(name))
        found[name].append([str(missing) for missing in portable.self_check()])
        if way == "another error":
            sys.meta_path.pop(0)
        sys.modules[name] = kept
print(json.dumps(found))
"""


def test_the_self_check_imports_each_module_of_rule_9_itself():
    """In a fresh interpreter, once everything imports: each module of rule 9, made missing or made to fail with an error that is no
    ImportError, comes back alone. The page library is imported through the review page's import_gradio()."""
    done = subprocess.run([sys.executable, "-B", "-c", SELF_CHECK_EACH], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.splitlines()[-1])
    assert list(found) == list(RULE_9)
    for name, results in found.items():
        assert results == [[name], [name]], (name, results)


def test_a_folder_that_cannot_be_written_says_so_in_the_window(tmp_path, capsys, monkeypatch):
    """A folder where the user may not write (C:\\Program Files for a standard user, a read-only share): the window says where to
    put the folder, and not that the reason is in a start.log that cannot exist."""
    folder = tmp_path / "Inkoopassistent"
    folder.mkdir()
    real = Path.mkdir

    def refused(self, *args, **kwargs):
        if folder in self.parents:
            raise PermissionError(13, "made-up: access is denied", str(self))
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", refused)
    launcher = Launcher({})
    assert portable.start(folder / "data", run=launcher, answers=Page(False), open_page=Browser(), wait_seconds=30) == 1
    assert said(capsys) == [UNWRITABLE] and launcher.calls == [] and list(folder.iterdir()) == []
    monkeypatch.undo()
    (folder / "data").mkdir()
    (folder / "data" / "logs").write_text("a file where the folder logs should be", encoding="utf-8")
    assert portable.start(folder / "data", run=launcher, answers=Page(False), open_page=Browser(), wait_seconds=30) == 1
    assert said(capsys) == [UNWRITABLE] and launcher.calls == []


def test_every_windows_call_that_fails_gives_one_dutch_line_and_one_line_in_the_log(tmp_path, capsys, quick):
    data = tmp_path / "data"
    browser = Browser()
    # the launcher cannot be run at all
    error = Launcher({}, error=FileNotFoundError(2, "made-up: no such file"))
    assert portable.start(data, run=error, answers=Page(False), open_page=browser, wait_seconds=1) == 1
    assert said(capsys) == [STARTING, NOT_STARTED]
    assert log(data)[-1] == "folder start: --install-autostart could not be run (FileNotFoundError: [Errno 2] made-up: no such file)"
    assert log(data)[0] == "folder start: --status could not be run (FileNotFoundError: [Errno 2] made-up: no such file)"
    # the start at logon cannot be set
    launcher = Launcher({("--status",): 1, ("--install-autostart",): 1})
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=1) == 1
    assert said(capsys) == [STARTING, NOT_STARTED]
    assert log(data)[-1] == "folder start: --install-autostart ended with exit code 1: made-up line of --install-autostart"
    # --resume cannot start the launcher
    (data / "STOP").write_text("", encoding="utf-8")
    launcher = Launcher({("--status",): 1, ("--install-autostart",): 0, ("--resume", "--background"): 3})
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=1) == 1
    assert said(capsys) == [STARTING, NOT_STARTED] and log(data)[-1].startswith("folder start: --resume --background ended with exit code 3")
    (data / "STOP").unlink()
    # something else answers at the page's address while no launcher runs on these settings: nothing is started
    launcher = Launcher({("--status",): 1})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=browser, wait_seconds=1) == 1
    assert launcher.options() == [("--status",)] and said(capsys) == [STARTING, NOT_STARTED] and browser.opened == []
    assert f"something already answers at {PAGE} and {CAPTURE}" in log(data)[-1]
    # the browser cannot be opened
    launcher = Launcher({("--status",): 0})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=Browser(works=False), wait_seconds=1) == 1
    assert said(capsys) == [ALREADY_RUNNING, "De pagina kon niet vanzelf worden geopend. Open zelf http://127.0.0.1:7860 in uw browser."]
    assert log(data)[-1] == "folder start: the browser could not be opened"
    # the stop and the end of the start at logon
    assert portable.stop(data, run=error) == 1
    assert said(capsys) == [STOPPING, "De inkoopassistent kon niet stoppen. De reden staat in de map data, in logs, in het bestand start.log."]
    assert log(data)[-1].startswith("folder stop: --stop could not be run (FileNotFoundError")
    assert portable.no_autostart(data, run=Launcher({("--remove-autostart",): 1})) == 1
    assert said(capsys) == ["Het vanzelf starten kon niet worden uitgezet. De reden staat in de map data, in logs, in het bestand start.log."]
    assert log(data)[-1] == "folder no-autostart: --remove-autostart ended with exit code 1: made-up line of --remove-autostart"


def test_a_fault_of_the_start_is_caught_by_the_command(tmp_path, capsys, monkeypatch):
    """A fault of the start itself: its traceback goes to start.log, the window says that the assistant could not start, and the
    exit code is 1; a Ctrl-C ends the command with 130."""
    data = tmp_path / "data"

    def fault(*args, **kwargs):
        raise RuntimeError("made-up fault of the start")
    monkeypatch.setattr(portable, "first_start", fault)
    assert portable.main(["--start", str(data)]) == 1
    assert said(capsys) == [NOT_STARTED]
    lines = log(data)
    assert lines[0] == "folder start: the command failed (RuntimeError)" and lines[1] == "Traceback (most recent call last):"
    assert lines[-1] == "RuntimeError: made-up fault of the start"

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(portable, "first_start", interrupted)
    assert portable.main(["--start", str(data)]) == 130


# --- the start without a console: the message boxes, and the start lock ---

def made_up_fault(*args, **kwargs):
    raise RuntimeError("made-up fault of the start")


def test_with_a_console_the_start_prints_its_lines_as_before_and_shows_no_box(tmp_path, capsys, quick, monkeypatch):
    """A console: a start file of an earlier version, which runs python.exe in a window, or python.exe started by hand. The start
    prints its lines as before, a fault's line too, and shows no box."""
    data = tmp_path / "data"
    boxes = Boxes()
    launcher = Launcher({("--status",): [1, 1, 0], ("--install-autostart",): 0})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30, box=boxes) == 0
    assert said(capsys) == [STARTING, STARTING_UP]
    launcher = Launcher({("--status",): 0})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=Browser(works=False), wait_seconds=30, box=boxes) == 1
    assert said(capsys) == [ALREADY_RUNNING, NOT_OPENED]
    launcher = Launcher({("--status",): 1, ("--install-autostart",): 1})
    assert portable.start(data, run=launcher, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert said(capsys) == [STARTING, NOT_STARTED]
    monkeypatch.setattr(portable, "BOX", boxes)
    monkeypatch.setattr(portable, "first_start", made_up_fault)
    assert portable.main(["--start", str(data)]) == 1
    assert said(capsys) == [NOT_STARTED] and boxes.events == []


def test_without_a_console_a_start_that_finds_its_launcher_running_shows_no_box(tmp_path, quick, windowless):
    """The start file runs pythonw.exe, which has no console. When a launcher already runs on these settings, the page opens and no
    box is shown."""
    windowless()
    data = tmp_path / "data"
    boxes = Boxes()
    browser = Browser(boxes=boxes)
    launcher = Launcher({("--status",): 0})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=browser, wait_seconds=30, box=boxes) == 0
    assert launcher.options() == [("--status",)] and browser.opened == [PAGE] and boxes.events == []
    assert log(data)[-2:] == ["folder start: a launcher runs on these settings: only the page is opened",
                              "folder start: the page answers and was opened in the browser"]


def test_without_a_console_the_start_shows_one_information_box_and_closes_it_before_the_page_opens(tmp_path, quick, windowless):
    """When it really starts, one information box says so, with the note that the first time can take a few minutes. It is shown
    from a daemon thread of its own, so that the start goes on while it stands and it never keeps the process alive, and the start
    closes it itself before it opens the page. That the start still waits for the launcher (STARTING_UP) is no box of its own."""
    windowless()
    data = tmp_path / "data"
    boxes = Boxes()
    browser = Browser(boxes=boxes)
    launcher = Launcher({("--status",): [1, 1, 1, 0], ("--install-autostart",): 0})
    assert portable.start(data, run=launcher, answers=Page(False, False, False, True), open_page=browser, wait_seconds=30,
                          box=boxes) == 0
    assert launcher.options() == [("--status",), ("--install-autostart",), ("--status",), ("--status",), ("--status",)]
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX)]
    assert browser.opened == [PAGE] and browser.standing == [[]]                         # gone when the page opens
    [(thread, daemon)] = boxes.threads
    assert thread != threading.get_ident() and daemon
    assert not [alive for alive in threading.enumerate() if alive.ident == thread]        # its thread has ended


# Each way the start fails: the launcher's exit codes, the answers of the addresses, whether the browser opens, the wait for the
# page, what else fails (a function of the module made to fail), the Dutch line, and whether the information box stands first (not
# when a launcher already runs, nor before --status).
FAILURES = {
    "something answers at an address": ({("--status",): 1}, [True], True, 30, None, NOT_STARTED, True),
    "a module cannot be imported": ({("--status",): 1}, [False], True, 30, ("self_check", lambda: ["PIL"]), NOT_STARTED, True),
    "the start at logon cannot be set": ({("--status",): 1, ("--install-autostart",): 1}, [False], True, 30, None, NOT_STARTED, True),
    "no launcher comes up": ({("--status",): 1, ("--install-autostart",): 0}, [False], True, 30, None, NOT_STARTED, True),
    "the page does not answer": ({("--status",): [1, 0], ("--install-autostart",): 0}, [False], True, 0.05, None, NOT_YET, True),
    "no browser opens": ({("--status",): [1, 0], ("--install-autostart",): 0}, [False, False, True], False, 30, None, NOT_OPENED, True),
    "a launcher runs and the page does not answer": ({("--status",): 0}, [False], True, 0.05, None, NOT_YET, False),
    "a launcher runs and no browser opens": ({("--status",): 0}, [True], False, 30, None, NOT_OPENED, False),
    "the settings cannot be written": ({}, [False], True, 30, ("first_start", lambda data: unwritable(data)), NOT_STARTED, False),
}


def unwritable(data: Path) -> Path:
    raise PermissionError(13, "made-up: access is denied", str(data / "settings.json"))


@pytest.mark.parametrize("case", list(FAILURES))
def test_without_a_console_a_failure_closes_the_information_box_and_shows_one_warning(case, tmp_path, monkeypatch, quick, windowless):
    """Each failure shows one warning box with its Dutch line, which waits for OK in the start's own thread: after the information
    box was closed, and after the start lock was removed, so that a double-click while the warning stands starts again."""
    windowless()
    codes, answers, works, wait, broken, line, informed = FAILURES[case]
    if broken is not None:
        monkeypatch.setattr(portable, *broken)
    data = tmp_path / "data"
    boxes = Boxes(lock=data / "start.lock")
    launcher = Launcher(codes)
    assert portable.start(data, run=launcher, answers=Page(*answers), open_page=Browser(works=works), wait_seconds=wait, box=boxes) == 1
    information = [("information", STARTING_BOX), ("closed", STARTING_BOX)] if informed else []
    assert boxes.events == [*information, ("warning", line)]
    assert boxes.threads[-1] == (threading.get_ident(), threading.current_thread().daemon) and boxes.lock_stood[-1] is False
    assert not (data / "start.lock").exists()


def test_without_a_console_the_start_as_the_start_file_runs_it_shows_the_boxes_of_windows(tmp_path, monkeypatch, quick, windowless):
    """main() gives start() no boxes: the start that the start file runs shows Windows' own (BOX), read when start is called. Through
    BOX, a real start shows its information box and closes it before the page opens, a failure after it shows the warning, a failure
    while a launcher runs shows the warning alone, and so does a folder that cannot be written."""
    windowless()
    assert inspect.signature(portable.start).parameters["box"].default is None
    data = tmp_path / "data"
    boxes = Boxes(lock=data / "start.lock")
    monkeypatch.setattr(portable, "BOX", boxes)
    browser = Browser(boxes=boxes)
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=browser, wait_seconds=30) == 0
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX)]
    assert browser.opened == [PAGE] and browser.standing == [[]]
    launcher = Launcher({("--status",): 1, ("--install-autostart",): 1})
    assert portable.start(data, run=launcher, answers=Page(False), open_page=browser, wait_seconds=30) == 1
    assert boxes.events[2:] == [("information", STARTING_BOX), ("closed", STARTING_BOX), ("warning", NOT_STARTED)]
    assert boxes.lock_stood[-1] is False
    launcher = Launcher({("--status",): 0})
    assert portable.start(data, run=launcher, answers=Page(True), open_page=Browser(works=False), wait_seconds=30) == 1
    assert boxes.events[5:] == [("warning", NOT_OPENED)]
    unwritable = tmp_path / "unwritable" / "data"
    unwritable.mkdir(parents=True)
    (unwritable / "logs").write_text("a file where the folder logs should be", encoding="utf-8")
    assert portable.start(unwritable, run=Launcher({}), answers=Page(False), open_page=Browser(), wait_seconds=30) == 1
    assert boxes.events[6:] == [("warning", UNWRITABLE)]


def test_under_pythonw_the_start_shows_its_boxes_also_when_it_was_given_an_output(tmp_path, monkeypatch, capsys, quick):
    """pythonw.exe has no console of its own: a start under it shows its boxes also when Windows handed it an output, in which no
    one would read its lines."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Inkoopassistent" / "python" / "pythonw.exe"))
    boxes = Boxes()
    launcher = Launcher({("--status",): 1})
    assert portable.start(tmp_path / "data", run=launcher, answers=Page(True), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX), ("warning", NOT_STARTED)]
    assert said(capsys) == []


def test_without_a_console_a_folder_that_cannot_be_written_says_so_in_a_warning_box(tmp_path, windowless):
    windowless()
    folder = tmp_path / "Inkoopassistent"
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "logs").write_text("a file where the folder logs should be", encoding="utf-8")
    boxes, launcher = Boxes(), Launcher({})
    assert portable.start(folder / "data", run=launcher, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("warning", UNWRITABLE)] and launcher.calls == [] and not (folder / "data" / "start.lock").exists()


def test_without_a_console_the_start_closes_an_information_box_whose_window_comes_late(tmp_path, quick, windowless):
    """Windows makes the window of a box a moment after the box is asked for: a start that fails at once tries again until it
    finds the window and closes it, before it shows its warning."""
    windowless()
    boxes = Boxes(delay=0.3)
    launcher = Launcher({("--status",): 1})
    assert portable.start(tmp_path / "data", run=launcher, answers=Page(True), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX), ("warning", NOT_STARTED)]


def test_without_a_console_a_fault_of_the_start_shows_a_warning_box(tmp_path, monkeypatch, windowless):
    """main() catches a fault of the start: its traceback goes to start.log, and without a console one warning box says that the
    assistant could not start, once the start lock is removed."""
    windowless()
    data = tmp_path / "data"
    boxes = Boxes(lock=data / "start.lock")
    monkeypatch.setattr(portable, "BOX", boxes)
    monkeypatch.setattr(portable, "first_start", made_up_fault)
    assert portable.main(["--start", str(data)]) == 1
    assert boxes.events == [("warning", NOT_STARTED)] and boxes.lock_stood == [False]
    assert boxes.threads == [(threading.get_ident(), threading.current_thread().daemon)]
    lines = log(data)
    assert lines[0] == "folder start: the command failed (RuntimeError)" and lines[1] == "Traceback (most recent call last):"
    assert lines[-1] == "RuntimeError: made-up fault of the start"


def test_a_fault_after_the_information_box_reaches_the_command_with_the_box_closed_and_the_lock_removed(tmp_path, monkeypatch, quick,
                                                                                                         windowless):
    """A fault of the start after its information box: the start closes the box and removes its lock before the fault reaches
    main(), which then shows the warning."""
    windowless()
    data = tmp_path / "data"
    boxes = Boxes()
    monkeypatch.setattr(portable, "self_check", made_up_fault)
    with pytest.raises(RuntimeError, match="made-up fault of the start"):
        portable.start(data, run=Launcher({("--status",): 1}), answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes)
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX)] and not (data / "start.lock").exists()


def test_a_second_start_while_the_first_is_busy_ends_at_once_and_shows_nothing(tmp_path, quick, windowless):
    """A second double-click during a slow first start: the second start finds the first one's start lock in the data folder,
    writes one line in start.log and ends with 0 at once, without a box and without a command of the launcher; the first start
    opens the page. One --install-autostart in all."""
    windowless()
    data = tmp_path / "data"
    boxes, page = Boxes(), Page(False, False, True)
    browser, second = Browser(boxes=boxes), []

    def double_click():
        second.append(portable.start(data, run=launcher, answers=page, open_page=browser, wait_seconds=30, box=boxes))
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0}, effects={("--install-autostart",): double_click})
    assert portable.start(data, run=launcher, answers=page, open_page=browser, wait_seconds=30, box=boxes) == 0
    assert second == [0]
    assert launcher.options() == [("--status",), ("--install-autostart",), ("--status",)]
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX)] and browser.opened == [PAGE]
    [line] = [line for line in log(data) if "still busy" in line]
    assert line.startswith("folder start: another start is still busy (start.lock of ") and f", process {os.getpid()})" in line
    assert line.endswith("): this one ends; that one opens the page or says why it could not")
    assert not (data / "start.lock").exists()


def test_with_a_console_a_second_start_while_the_first_is_busy_prints_nothing(tmp_path, capsys, quick):
    data = tmp_path / "data"
    page, browser, second = Page(False, False, True), Browser(), []

    def double_click():
        second.append(portable.start(data, run=launcher, answers=page, open_page=browser, wait_seconds=30))
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0}, effects={("--install-autostart",): double_click})
    assert portable.start(data, run=launcher, answers=page, open_page=browser, wait_seconds=30) == 0
    assert second == [0] and said(capsys) == [STARTING] and browser.opened == [PAGE]
    assert [options for options in launcher.options() if options == ("--install-autostart",)] == [("--install-autostart",)]


def test_a_start_lock_left_by_a_start_that_did_not_end_is_taken_over(tmp_path, quick, windowless):
    """A start that was ended before it could remove its lock (a logoff, Task Manager) leaves it behind: a lock older than the
    launcher's wait, the page's wait and LOCK_MARGIN together is taken over, with a line in start.log, and the start goes on; a
    younger one is a start that is still busy. A lock that cannot be read (a start that writes it in this very moment) is judged
    by the time of the file."""
    windowless()
    data = tmp_path / "data"
    portable.first_start(data)
    lock = data / "start.lock"
    limit = portable.LAUNCHER_WAIT + 30 + portable.LOCK_MARGIN

    def start() -> tuple[int, Launcher, Boxes]:
        launcher, boxes = Launcher({("--status",): [1, 0], ("--install-autostart",): 0}), Boxes()
        code = portable.start(data, run=launcher, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30, box=boxes)
        return code, launcher, boxes
    for age, left in ((limit + 5, True), (limit - 5, False)):
        lock.write_text(json.dumps({"time": time.time() - age, "pid": 4321}), encoding="utf-8")
        held = lock.read_bytes()
        code, launcher, boxes = start()
        assert code == 0, age
        if left:
            assert launcher.options() == [("--status",), ("--install-autostart",), ("--status",)] and len(boxes.events) == 2
            [line] = [line for line in log(data) if "taken over" in line]
            assert line.startswith("folder start: the start lock (start.lock of ") and line.endswith(
                f", process 4321) is older than {limit:g} seconds, left by a start that did not end: taken over")
            assert not lock.exists()
        else:
            assert launcher.calls == [] and boxes.events == [] and lock.read_bytes() == held
    for text in ("", "{not json", "[]", '{"time": "now", "pid": 1}', '{"time": 1e300, "pid": 1}', '{"time": NaN, "pid": 1}',
                 '{"time": 1, "pid": Infinity}'):                                   # being written: fresh by the file's time
        lock.write_text(text, encoding="utf-8")
        code, launcher, boxes = start()
        assert code == 0 and launcher.calls == [] and boxes.events == [] and lock.read_text(encoding="utf-8") == text, text
    unread = [line for line in log(data) if line.startswith("folder start: another start is still busy (start.lock of ")
              and "process" not in line]
    assert len(unread) == 7 and all(line.endswith("): this one ends; that one opens the page or says why it could not") for line in unread)
    os.utime(lock, (time.time() - limit - 5, time.time() - limit - 5))
    code, launcher, boxes = start()
    assert code == 0 and launcher.options()[:2] == [("--status",), ("--install-autostart",)] and not lock.exists()


def test_the_start_removes_its_own_lock_only(tmp_path, quick, windowless):
    """The start's lock holds the time and the process id, and the start removes it at its end. A lock that another start took
    over meanwhile (one that found this one's lock older than the limit) is that start's, and stays."""
    windowless()
    data = tmp_path / "data"
    lock, held = data / "start.lock", []
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0},
                        effects={("--install-autostart",): lambda: held.append(json.loads(lock.read_text(encoding="utf-8")))})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30, box=Boxes()) == 0
    [mine] = held
    assert sorted(mine) == ["pid", "time"] and mine["pid"] == os.getpid() and abs(mine["time"] - time.time()) < 60
    assert not lock.exists()
    other = json.dumps({"time": time.time(), "pid": 4321})
    launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0},
                        effects={("--install-autostart",): lambda: lock.write_text(other, encoding="utf-8")})
    assert portable.start(data, run=launcher, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30, box=Boxes()) == 0
    assert lock.read_text(encoding="utf-8") == other


def test_the_message_boxes_are_windows_own_and_close_only_the_box_of_their_thread():
    """BOX shows a box of Windows (MessageBoxW of user32) with the title Inkoopassistent: an information with its icon, and a
    warning with its icon, above the other windows, since it may come minutes after the double-click. close posts WM_CLOSE to the
    box of the given thread only: never to the Explorer window of the folder, whose title is the same, nor to a box of another
    start."""
    user32 = User32([(101, "CabinetWClass", "Inkoopassistent", 7), (102, "#32770", "Inkoopassistent", 8),
                     (103, "#32770", "Inkoopassistent", 9)])
    boxes = portable._MessageBoxes(user32)
    boxes(STARTING_BOX, False)
    boxes(NOT_STARTED, True)
    information, warning, foreground, topmost = 0x40, 0x30, 0x10000, 0x40000              # MB_ICONINFORMATION and the others
    assert user32.shown == [(None, STARTING_BOX, "Inkoopassistent", information | foreground),
                            (None, NOT_STARTED, "Inkoopassistent", warning | foreground | topmost)]
    assert boxes.close(9) is True and user32.posted == [(103, 0x0010, 0, 0)]               # WM_CLOSE
    assert boxes.close(5) is False and len(user32.posted) == 1
    assert set(user32.asked) == {("#32770", "Inkoopassistent")}                           # a dialog's class, the boxes' title
    assert portable.TITLE == "Inkoopassistent"


@pytest.mark.skipif(os.name == "nt", reason="on Windows these would be the real boxes")
def test_off_windows_the_message_boxes_show_nothing_and_never_load_ctypes(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctypes", None)                                      # an import of ctypes fails
    boxes = portable._MessageBoxes()
    assert boxes(NOT_STARTED, True) is None and boxes.close(1) is False


def test_on_windows_the_message_boxes_load_user32_with_the_types_windows_declares(monkeypatch):
    """The set-up of the boxes on Windows, here with a WinDLL of the test and ctypes' own types, so that a type that ctypes does not
    have fails the test: user32 is loaded once, and each of its four calls gets the types of its arguments and of its result as
    Windows declares them; a window handle is as wide as a pointer, so that the handle of a box on a 64-bit Windows is not cut."""
    import ctypes
    from ctypes import wintypes
    loaded = []

    class WinDLL:
        def __init__(self, name: str, **options):
            loaded.append((name, options))
            self.calls = {}

        def __getattr__(self, name: str):
            if name.startswith("_"):
                raise AttributeError(name)
            return self.calls.setdefault(name, types.SimpleNamespace(argtypes=None, restype=None))
    monkeypatch.setattr(ctypes, "WinDLL", WinDLL, raising=False)
    monkeypatch.setattr(portable, "os", types.SimpleNamespace(name="nt"))                 # Windows, for _windows alone
    boxes = portable._MessageBoxes()
    user32 = boxes._windows()
    assert boxes._windows() is user32 and loaded == [("user32", {"use_last_error": True})]
    assert {name: (call.argtypes, call.restype) for name, call in user32.calls.items()} == {
        "MessageBoxW": ([wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT], ctypes.c_int),
        "FindWindowExW": ([wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR], wintypes.HWND),
        "GetWindowThreadProcessId": ([wintypes.HWND, wintypes.LPDWORD], wintypes.DWORD),
        "PostMessageW": ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL)}
    assert ctypes.sizeof(user32.calls["FindWindowExW"].restype) == ctypes.sizeof(ctypes.c_void_p)


class Failing:
    """Message boxes whose calls fail as a call of Windows can: showing a box raises (show False); or the information box stands,
    until the test releases it, while closing it raises (close "raises") or never finds it (close "misses"). It keeps each ask. A
    close waits until the information box has been asked for, as Windows can only close a box that is there."""

    def __init__(self, show: bool = True, close: str = "misses"):
        self.show, self.how, self.asked = show, close, []
        self.asked_for, self.released = threading.Event(), threading.Event()

    def __call__(self, text: str, warning: bool) -> None:
        self.asked.append(("warning" if warning else "information", text))
        if not warning:
            self.asked_for.set()
        if not self.show:
            raise RuntimeError("made-up: no box can be shown")
        if not warning:
            self.released.wait(5)

    def close(self, thread: int) -> bool:
        self.asked_for.wait(5)
        self.asked.append(("close", thread))
        if self.how == "raises":
            raise RuntimeError("made-up: the box cannot be closed")
        return False

    def release(self) -> None:
        """Let the information box go, and wait until its thread has ended."""
        self.released.set()
        for thread in threading.enumerate():
            if thread.name == "information box":
                thread.join(5)


def box_lines(data: Path) -> list[str]:
    return [line for line in log(data) if " box could not be " in line]


def test_without_a_console_a_box_that_cannot_be_shown_gives_one_line_in_the_log(tmp_path, quick, windowless):
    """A message box that cannot be shown (the set-up of ctypes slipped, or ctypes cannot be loaded) leaves the buyer without a word:
    start.log says so, once per box, and the start ends as it would have."""
    windowless()
    data = tmp_path / "data"
    boxes = Failing(show=False)
    launcher = Launcher({("--status",): 1, ("--install-autostart",): 1})
    assert portable.start(data, run=launcher, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert [text for kind, text in boxes.asked if kind != "close"] == [STARTING_BOX, NOT_STARTED]
    assert sorted(box_lines(data)) == ["folder start: the information box could not be shown (RuntimeError: made-up: no box can be shown)",
                                       "folder start: the warning box could not be shown (RuntimeError: made-up: no box can be shown)"]
    assert log(data)[-1] == "folder start: the warning box could not be shown (RuntimeError: made-up: no box can be shown)"
    assert not (data / "start.lock").exists()


def test_without_a_console_an_information_box_that_cannot_be_closed_gives_one_line_in_the_log(tmp_path, monkeypatch, quick,
                                                                                                windowless):
    """Closing the information box raises: start.log says so once, and the start gives up on it at once, rather than a hundred
    times. A box that is never found to be closed (a handle cut short) gets one line once the start's wait for it is over. Either way
    the start goes on: the box goes with the start's process."""
    windowless()
    data = tmp_path / "data"
    boxes = Failing(close="raises")
    try:
        launcher = Launcher({("--status",): 1, ("--install-autostart",): 1})
        assert portable.start(data, run=launcher, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    finally:
        boxes.release()
    assert len([ask for ask in boxes.asked if ask[0] == "close"]) == 1 and boxes.asked[-1] == ("warning", NOT_STARTED)
    assert box_lines(data) == ["folder start: the information box could not be closed (RuntimeError: made-up: the box cannot be closed)"]
    monkeypatch.setattr(portable, "BOX_WAIT", 0.2)
    other = tmp_path / "other" / "data"
    boxes, browser = Failing(close="misses"), Browser()
    try:
        launcher = Launcher({("--status",): [1, 0], ("--install-autostart",): 0})
        assert portable.start(other, run=launcher, answers=Page(False, False, True), open_page=browser, wait_seconds=30, box=boxes) == 0
    finally:
        boxes.release()
    assert browser.opened == [PAGE] and len([ask for ask in boxes.asked if ask[0] == "close"]) > 1
    assert box_lines(other) == ["folder start: the information box could not be closed: it still stood after 0.2 seconds"]
    assert log(other)[-1] == "folder start: the page answers and was opened in the browser"


def test_without_a_console_a_warning_box_of_the_command_that_cannot_be_shown_gives_one_line_in_the_log(tmp_path, monkeypatch,
                                                                                                        windowless):
    """main() catches a fault of the start and shows the warning box: when that box cannot be shown either, start.log says so
    after the fault's traceback."""
    windowless()
    data = tmp_path / "data"
    monkeypatch.setattr(portable, "BOX", Failing(show=False))
    monkeypatch.setattr(portable, "first_start", made_up_fault)
    assert portable.main(["--start", str(data)]) == 1
    lines = log(data)
    assert lines[0] == "folder start: the command failed (RuntimeError)" and "RuntimeError: made-up fault of the start" in lines
    assert lines[-1] == "folder start: the warning box could not be shown (RuntimeError: made-up: no box can be shown)"


NO_CONSOLE = """
import sys
sys.stdin = sys.stdout = sys.stderr = sys.__stdin__ = sys.__stdout__ = sys.__stderr__ = None    # as under pythonw.exe
from aid import portable
missing = portable.self_check()
with open(sys.argv[1], "w", encoding="utf-8") as out:
    out.write(repr([f"{name}: {name.reason}" for name in missing]))
"""


def test_the_self_check_imports_every_module_without_a_console(tmp_path):
    """Under pythonw.exe, which the start file runs, sys.stdin, sys.stdout and sys.stderr are None, so a module that used them while
    it is imported would fail the self-check of every start without a window. In a fresh interpreter without them, every module of
    rule 9 imports."""
    out = tmp_path / "missing.txt"
    done = subprocess.run([sys.executable, "-B", "-c", NO_CONSOLE, str(out)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8") == "[]"


def test_stop_runs_the_launchers_stop(tmp_path, capsys):
    data = tmp_path / "data"
    portable.first_start(data)
    state = data / "start_state.json"
    state.write_text("{}", encoding="utf-8")                                                 # a launcher runs
    launcher = Launcher({("--stop",): 0}, effects={("--stop",): state.unlink})
    assert portable.stop(data, run=launcher) == 0
    assert [cmd for cmd, _ in launcher.calls] == [command(data, "--stop")]
    assert said(capsys) == [STOPPING, STOPPED]
    assert log(data)[-1] == "folder stop: --stop ended with exit code 0: made-up line of --stop"
    assert portable.STATE_FILE == __import__("aid.start").start.STATE_FILE
    # --no-autostart runs the launcher's --remove-autostart
    launcher = Launcher({("--remove-autostart",): 0})
    assert portable.no_autostart(data, run=launcher) == 0
    assert [cmd for cmd, _ in launcher.calls] == [command(data, "--remove-autostart")] and said(capsys) == [NO_AUTOSTART]
    # a folder that never started has nothing to stop, and its data folder is not made
    never = tmp_path / "never" / "data"
    launcher = Launcher({})
    assert portable.stop(never, run=launcher) == 0 and launcher.calls == [] and not never.exists()
    assert said(capsys) == [STOPPING, STOPPED]


def test_the_stop_says_it_stopped_only_when_the_launcher_has_ended(tmp_path, capsys):
    """--stop ends with 0 also when the parts have not ended within its 90 seconds: while the launcher's state file is still there,
    the window says to wait before the folder is moved or deleted."""
    data = tmp_path / "data"
    portable.first_start(data)
    (data / "start_state.json").write_text("{}", encoding="utf-8")
    assert portable.stop(data, run=Launcher({("--stop",): 0})) == 0
    assert said(capsys) == [STOPPING, STILL_STOPPING]
    assert log(data)[-1] == "folder stop: the launcher's state file is still there: the launcher is still stopping its parts"


def test_the_command_hands_each_option_to_its_function(tmp_path, monkeypatch):
    seen = []
    for name in ("build", "start", "stop", "no_autostart", "demo_start", "demo_stop"):
        monkeypatch.setattr(portable, name, lambda *args, name=name, **kwargs: seen.append((name, args, kwargs)) or 0)
    assert portable.main(["--build", str(tmp_path / "out"), "--python-zip", str(tmp_path / "python.zip")]) == 0
    assert portable.main(["--start", str(tmp_path / "data")]) == 0
    assert portable.main(["--stop", str(tmp_path / "data")]) == 0
    assert portable.main(["--no-autostart", str(tmp_path / "data")]) == 0
    assert portable.main(["--demo", str(tmp_path / "demo")]) == 0
    assert portable.main(["--demo-stop", str(tmp_path / "demo")]) == 0
    assert seen == [("build", (tmp_path / "out",), {"python_zip": tmp_path / "python.zip"}), ("start", (tmp_path / "data",), {}),
                    ("stop", (tmp_path / "data",), {}), ("no_autostart", (tmp_path / "data",), {}),
                    ("demo_start", (tmp_path / "demo",), {}), ("demo_stop", (tmp_path / "demo",), {})]
    with pytest.raises(SystemExit):
        portable.main(["--start", str(tmp_path / "data"), "--stop", str(tmp_path / "data")])


def test_the_module_imports_with_the_standard_library_only():
    """The start must reach its self-check, and the build needs none of the aid's packages: aid.portable imports nothing else, and
    ctypes only when it shows a message box on Windows."""
    script = ("import sys\nfor name in ('pydantic', 'gradio', 'PIL', 'msal', 'anthropic', 'requests', 'ctypes'):\n    sys.modules[name] = None\n"
              "import aid.portable\nprint('ok')\n")
    done = subprocess.run([sys.executable, "-B", "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout == "ok\n", done.stderr
    assert dict(portable.ADDRESSES) == dict(__import__("aid.start").start.ADDRESSES)



# --- the demonstration start ---

DEMO_STARTING = "De demonstratie van de inkoopassistent start, met verzonnen documenten. Even geduld: de pagina opent vanzelf in uw browser."
DEMO_STARTING_BOX = DEMO_STARTING + " Dat kan een paar minuten duren."
DEMO_ALREADY_RUNNING = "De demonstratie draait al. De pagina wordt geopend."
DEMO_NOT_STARTED = "De demonstratie kon niet starten. De reden staat in de map demo, in logs, in het bestand start.log."
DEMO_FOREIGN = ("Er staat al een map demo die Demonstratie starten niet heeft gemaakt. Er is niets verwijderd en niets gestart: geef die "
                "map een andere naam of zet hem ergens anders, en dubbelklik dan opnieuw op Demonstratie starten.")
REAL_RUNS = ("De inkoopassistent zelf draait. De demonstratie kan pas starten als die stilstaat: dubbelklik eerst op Inkoopassistent "
             "stoppen en daarna opnieuw op Demonstratie starten.")
PORT_TAKEN = ("Er antwoordt al iets anders op het adres van de pagina (http://127.0.0.1:7860 of 7870). Sluit dat eerst en dubbelklik "
              "dan opnieuw op Demonstratie starten.")
DEMO_RUNS = ("De demonstratie draait nog. Dubbelklik eerst op Demonstratie stoppen en daarna opnieuw op Inkoopassistent starten.")
DEMO_STOPPING = "De demonstratie stopt. Dit duurt hoogstens anderhalve minuut."
DEMO_STOPPED = "De demonstratie is gestopt. Dubbelklik op Demonstratie starten om haar opnieuw te beginnen."
DEMO_STILL_STOPPING = "De demonstratie stopt nog. Wacht een minuut voordat u haar opnieuw start."
DEMO_LAUNCH = ("--testshop", "--poll-seconds", "10", "--background")


class Folders:
    """A fake subprocess.run for the demonstration start: the launcher's commands answered per folder of the settings ("data" or
    "demo") and options, as Launcher answers them; and the filling of the demonstration folder (python -m aid.demo), run here
    through aid.demo itself, so that the folder holds what the real command gives it. Every command is kept in its order."""

    def __init__(self, codes: dict[tuple, int | list[int]], effects: dict | None = None, fill_code: int | None = None):
        self.codes = {key: list(code) if isinstance(code, list) else [code] for key, code in codes.items()}
        self.effects, self.fill_code, self.calls = effects or {}, fill_code, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        assert cmd[:4] == [sys.executable, "-I", "-X", "utf8"] and kwargs["stdin"] == subprocess.DEVNULL and kwargs["capture_output"]
        if cmd[4:6] == ["-m", "aid.demo"]:
            if self.fill_code is not None:
                return subprocess.CompletedProcess(cmd, self.fill_code, "", "made-up error of the filling\n")
            from aid import demo
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = demo.main(cmd[6:])
            return subprocess.CompletedProcess(cmd, code, out.getvalue(), "")
        settings = Path(cmd[cmd.index("--settings") + 1])
        key = (settings.parent.name, *cmd[cmd.index("--settings") + 2:])
        if key in self.effects:
            self.effects[key]()
        codes = self.codes[key]
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        return subprocess.CompletedProcess(cmd, code, f"made-up line of {' '.join(key)}\n", "")

    def asked(self) -> list[tuple]:
        return [("fill",) if cmd[4:6] == ["-m", "aid.demo"] else (Path(cmd[cmd.index("--settings") + 1]).parent.name,
                                                                  *cmd[cmd.index("--settings") + 2:]) for cmd, _ in self.calls]


def demo_folder(tmp_path: Path) -> Path:
    """The folder of the folder version as the build makes it, with its folder data started once: the demonstration's folder
    demo beside it does not exist yet."""
    root = tmp_path / "Inkoopassistent"
    portable.first_start(root / "data")
    return root / "demo"


def test_the_demonstration_start_fills_its_own_folder_and_opens_the_page_on_it(tmp_path, capsys, quick):
    """One double click: a folder demo of its own beside data, marked as the demonstration start's, filled as python -m aid.demo
    --out fills one (the 22 made-up cases, the parser, no AI) with the settings of the demonstration (the test mode on, questions
    written as files), the launcher started on it in the background with the runner's short poll and the test shop, never the start
    at logon, and the page opened. The folder data is not changed, and its settings are never handed to a command but --status."""
    demo = demo_folder(tmp_path)
    data = demo.parent / "data"
    data_before = files_of(data)
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): 0, ("demo", *DEMO_LAUNCH): 0})
    page, browser = Page(False, False, True), Browser()
    assert portable.demo_start(demo, run=folders, answers=page, open_page=browser, wait_seconds=30) == 0
    assert folders.asked() == [("data", "--status"), ("fill",), ("demo", *DEMO_LAUNCH), ("demo", "--status")]
    assert not [cmd for cmd, _ in folders.calls if "--install-autostart" in cmd]                   # no start at logon
    [fill] = [cmd for cmd, _ in folders.calls if cmd[4:6] == ["-m", "aid.demo"]]
    assert fill[6:] == ["--out", str(demo.resolve()), "--force", "--demonstration"]
    assert page.asked == [PAGE, CAPTURE, PAGE] and browser.opened == [PAGE]
    assert said(capsys) == [DEMO_STARTING]
    assert files_of(data) == data_before                                                         # data untouched
    assert (demo / "DEMONSTRATIE.txt").read_text(encoding="utf-8") == portable.DEMO_MARKER_TEXT
    settings = load_settings(demo / "settings.json")
    assert settings.test_mode and settings.send_via == "file" and settings.cases_root == demo.resolve() / "cases"
    assert len([case for case in settings.cases_root.iterdir() if case.name[:1].isdigit()]) == 22
    assert settings.article_columns and sorted(p.name for p in (demo / "voorbeelden").iterdir()) == [
        "Verzonnen export artikelen S04 uit Exact.xlsx", "Verzonnen prijslijst S04.xlsx"]        # the tab Prijslijst works at once
    lines = log(demo)
    assert lines[0] == "demonstration start: --status ended with exit code 1 on the settings of the folder data: made-up line of data --status"
    assert lines[1].startswith("demonstration start: the made-up cases were written into the folder demo (python -m aid.demo ended "
                               "with exit code 0: ")
    assert "22 made-up cases run with the parser reader" in lines[1] and "Settings of the demonstration" in lines[1]
    assert lines[2:] == [f"demonstration start: {' '.join(DEMO_LAUNCH)} ended with exit code 0: made-up line of demo {' '.join(DEMO_LAUNCH)}",
                         "demonstration start: --status ended with exit code 0: made-up line of demo --status",
                         "demonstration start: the page answers and was opened in the browser"]
    assert not (demo / "start.lock").exists()
    # a second start begins fresh: what the first left (a case made with a try-out, a part's output, a STOP) is gone, the marker and
    # start.log stay
    (settings.cases_root / "250103_20261117T101500").mkdir()
    (demo / "logs" / "runner.out").write_text("made-up output", encoding="utf-8")
    (demo / "STOP").write_text("", encoding="utf-8")
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): [1, 0], ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=browser, wait_seconds=30) == 0
    assert folders.asked() == [("demo", "--status"), ("data", "--status"), ("fill",), ("demo", *DEMO_LAUNCH), ("demo", "--status")]
    assert not (settings.cases_root / "250103_20261117T101500").exists() and not (demo / "STOP").exists()
    assert not (demo / "logs" / "runner.out").exists() and len(log(demo)) == 11
    assert len([case for case in settings.cases_root.iterdir() if case.name[:1].isdigit()]) == 22
    assert files_of(data) == data_before and said(capsys) == [DEMO_STARTING]


def test_a_folder_demo_marked_by_an_earlier_version_is_still_the_demonstrations_own(tmp_path, quick, windowless):
    """The marker's text grew a sentence on the sample files of the price list; a folder demo that an earlier version of the
    demonstration start marked is still its own: it is replaced as any other, and its marker then has the present text."""
    windowless()
    demo = demo_folder(tmp_path)
    demo.mkdir()
    (demo / "DEMONSTRATIE.txt").write_text(portable.DEMO_MARKER_EARLIER[0], encoding="ascii")
    (demo / "STOP").write_text("", encoding="utf-8")
    assert portable.DEMO_MARKER_EARLIER[0] != portable.DEMO_MARKER_TEXT
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): [1, 0], ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30,
                               box=Boxes()) == 0
    assert not (demo / "STOP").exists() and (demo / "settings.json").is_file()
    assert (demo / "DEMONSTRATIE.txt").read_text(encoding="ascii") == portable.DEMO_MARKER_TEXT
    assert "voorbeelden" in portable.DEMO_MARKER_TEXT and portable.DEMO_MARKER_TEXT.isascii()


def test_a_folder_demo_the_demonstration_start_did_not_make_is_refused_and_left_as_it_is(tmp_path, capsys, quick, windowless):
    """The previous demonstration is removed only when it carries the marker that the demonstration start wrote, with its text:
    a folder demo without it, or with another text in it, is refused with one warning box and left as it is, and nothing is
    started; and neither is anything written into it, not even a log."""
    windowless()
    demo = demo_folder(tmp_path)
    demo.mkdir()
    (demo / "mine.txt").write_text("the owner's own notes", encoding="utf-8")
    for marker in (None, "something else"):
        if marker is not None:
            (demo / "DEMONSTRATIE.txt").write_text(marker, encoding="utf-8")
        before, boxes, folders = files_of(demo), Boxes(), Folders({})
        assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
        assert boxes.events == [("warning", DEMO_FOREIGN)] and folders.calls == []
        assert files_of(demo) == before
    # a file named demo is refused as well
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "demo").write_text("x", encoding="utf-8")
    boxes = Boxes()
    assert portable.demo_start(tmp_path / "other" / "demo", run=Folders({}), answers=Page(False), open_page=Browser(), box=boxes) == 1
    assert boxes.events == [("warning", DEMO_FOREIGN)]


def test_the_demonstration_start_refuses_while_the_real_aid_or_anything_else_answers(tmp_path, quick, windowless):
    """The real aid and the demonstration use the same two addresses: while the aid of this folder runs (its launcher's --status on
    data's settings), or anything else answers at either address, the demonstration start says so in a warning box and ends, before
    it removes or fills anything."""
    windowless()
    demo = demo_folder(tmp_path)
    data = demo.parent / "data"
    data_before = files_of(data)
    # the real aid runs
    boxes, folders = Boxes(), Folders({("data", "--status"): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(True), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [("data", "--status")]
    assert boxes.events == [("information", DEMO_STARTING_BOX), ("closed", DEMO_STARTING_BOX), ("warning", REAL_RUNS)]
    assert log(demo)[-1] == ("demonstration start: the aid of this folder runs (--status on the settings of the folder data): the aid "
                             "and the demonstration use the same addresses; stop the aid first; nothing started")
    assert not (demo / "settings.json").exists() and files_of(data) == data_before
    # something else answers at the page's address
    boxes, folders = Boxes(), Folders({("data", "--status"): 1})
    assert portable.demo_start(demo, run=folders, answers=lambda url: url == PAGE, open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [("data", "--status")] and boxes.events[-1] == ("warning", PORT_TAKEN)
    assert log(demo)[-1] == (f"demonstration start: something already answers at {PAGE} while neither the aid of this folder nor its "
                             "demonstration runs: only one aid runs on a PC; nothing started")
    # a folder whose data was never started: no --status on it, and still the address is asked
    fresh = tmp_path / "fresh" / "demo"
    fresh.parent.mkdir()
    boxes, folders = Boxes(), Folders({})
    assert portable.demo_start(fresh, run=folders, answers=Page(True), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.calls == [] and boxes.events[-1] == ("warning", PORT_TAKEN) and not (fresh.parent / "data").exists()


def test_a_demonstration_that_runs_is_opened_and_kept(tmp_path, capsys, quick):
    """A double click while the demonstration runs opens its page, as the real start does, and removes nothing: a try-out case
    made a moment ago stays. A fresh start comes after Demonstratie stoppen."""
    demo = demo_folder(tmp_path)
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): 0, ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30) == 0
    said(capsys)
    (demo / "cases" / "250103_20261117T101500").mkdir()
    folders, browser = Folders({("demo", "--status"): 0}), Browser()
    assert portable.demo_start(demo, run=folders, answers=Page(True), open_page=browser, wait_seconds=30) == 0
    assert folders.asked() == [("demo", "--status")] and browser.opened == [PAGE] and said(capsys) == [DEMO_ALREADY_RUNNING]
    assert (demo / "cases" / "250103_20261117T101500").is_dir()
    assert log(demo)[-2:] == ["demonstration start: a launcher runs on the demonstration's settings: only the page is opened",
                              "demonstration start: the page answers and was opened in the browser"]


@pytest.mark.parametrize("failing", ["fill", "launch", "no launcher"])
def test_a_demonstration_start_that_fails_shows_its_warning(failing, tmp_path, quick, windowless):
    windowless()
    demo = demo_folder(tmp_path)
    codes = {("data", "--status"): 1, ("demo", "--status"): 0 if failing != "no launcher" else 1,
             ("demo", *DEMO_LAUNCH): 1 if failing == "launch" else 0}
    folders, boxes = Folders(codes, fill_code=1 if failing == "fill" else None), Boxes(lock=demo / "start.lock")
    assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("information", DEMO_STARTING_BOX), ("closed", DEMO_STARTING_BOX), ("warning", DEMO_NOT_STARTED)]
    assert boxes.lock_stood[-1] is False and (demo / "DEMONSTRATIE.txt").is_file()
    if failing == "fill":
        assert log(demo)[-1] == ("demonstration start: the made-up cases could not be written into the folder demo (python -m aid.demo "
                                 "ended with exit code 1: made-up error of the filling); nothing started")
        assert ("demo", *DEMO_LAUNCH) not in folders.asked()


def test_a_demonstration_start_whose_modules_cannot_be_imported_fills_nothing(tmp_path, quick, monkeypatch, windowless):
    windowless()
    monkeypatch.setattr(portable, "self_check", lambda: [portable._Missing.of("PIL", ImportError("made-up"))])
    demo = demo_folder(tmp_path)
    folders, boxes = Folders({("data", "--status"): 1}), Boxes()
    assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [("data", "--status")] and boxes.events[-1] == ("warning", DEMO_NOT_STARTED)
    assert log(demo)[-2:] == ["demonstration start: PIL cannot be imported (ImportError: made-up)",
                              "demonstration start: these modules cannot be imported: PIL; nothing started"]


def test_a_second_demonstration_start_while_the_first_is_busy_ends_at_once(tmp_path, quick, windowless):
    windowless()
    demo = demo_folder(tmp_path)
    demo.mkdir()
    (demo / "DEMONSTRATIE.txt").write_text(portable.DEMO_MARKER_TEXT, encoding="utf-8")
    (demo / "start.lock").write_text(json.dumps({"time": time.time(), "pid": 4242}), encoding="utf-8")
    folders, boxes = Folders({}), Boxes()
    assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 0
    assert folders.calls == [] and boxes.events == [] and (demo / "start.lock").exists()
    assert log(demo)[-1].startswith("demonstration start: another start is still busy (start.lock of ")


def test_a_demonstration_folder_that_cannot_be_written_says_so(tmp_path, monkeypatch, windowless):
    windowless()
    demo = demo_folder(tmp_path)
    monkeypatch.setattr(portable, "_writable", lambda folder: False)
    boxes = Boxes()
    assert portable.demo_start(demo, run=Folders({}), answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("warning", portable.DEMO_UNWRITABLE)] and "demo" in portable.DEMO_UNWRITABLE


def test_the_real_start_refuses_while_the_demonstration_runs(tmp_path, quick, windowless):
    """Likewise the real start: while the demonstration of this folder runs, it says so in a warning box and starts nothing; it asks
    the demonstration's launcher only when the folder demo carries the marker."""
    windowless()
    demo = demo_folder(tmp_path)
    data = demo.parent / "data"
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): 0, ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30,
                               box=Boxes()) == 0
    boxes, folders = Boxes(), Folders({("data", "--status"): 1, ("demo", "--status"): 0})
    assert portable.start(data, run=folders, answers=Page(True), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [("data", "--status"), ("demo", "--status")]
    assert boxes.events == [("information", STARTING_BOX), ("closed", STARTING_BOX), ("warning", DEMO_RUNS)]
    assert log(data)[-1] == ("folder start: the demonstration of this folder runs (--status on the settings of the folder demo): the "
                             "aid and the demonstration use the same addresses; stop the demonstration first; nothing started")
    # a demonstration that does not run: the real start goes on as before
    folders = Folders({("data", "--status"): [1, 0], ("demo", "--status"): 1, ("data", "--install-autostart"): 0})
    assert portable.start(data, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30, box=Boxes()) == 0
    assert folders.asked() == [("data", "--status"), ("demo", "--status"), ("data", "--install-autostart"), ("data", "--status")]


def test_the_demonstration_stop_runs_the_launchers_stop_on_the_demonstration(tmp_path, capsys, quick):
    demo = demo_folder(tmp_path)
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): 0, ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30) == 0
    said(capsys)
    state = demo / "start_state.json"
    state.write_text("{}", encoding="utf-8")
    folders = Folders({("demo", "--stop"): 0}, effects={("demo", "--stop"): state.unlink})
    assert portable.demo_stop(demo, run=folders) == 0
    assert folders.asked() == [("demo", "--stop")] and said(capsys) == [DEMO_STOPPING, DEMO_STOPPED]
    assert log(demo)[-1] == "demonstration stop: --stop ended with exit code 0: made-up line of demo --stop"
    state.write_text("{}", encoding="utf-8")
    assert portable.demo_stop(demo, run=Folders({("demo", "--stop"): 0})) == 0
    assert said(capsys) == [DEMO_STOPPING, DEMO_STILL_STOPPING]
    # a folder demo that the demonstration start did not make is not touched; one that does not exist has nothing to stop
    other = tmp_path / "other" / "demo"
    other.mkdir(parents=True)
    (other / "settings.json").write_text("{}", encoding="utf-8")
    folders = Folders({})
    assert portable.demo_stop(other, run=folders) == 1 and folders.calls == [] and said(capsys) == [DEMO_STOPPING, DEMO_FOREIGN]
    assert sorted(path.name for path in other.iterdir()) == ["settings.json"]
    assert portable.demo_stop(tmp_path / "never" / "demo", run=folders) == 0 and said(capsys) == [DEMO_STOPPING, DEMO_STOPPED]
    assert not (tmp_path / "never").exists()


def started_demo(demo: Path) -> None:
    """A demonstration started once, whose launcher has since been given STOP (Demonstratie stoppen) and still runs."""
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): 0, ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30,
                               box=Boxes()) == 0


DEMO_IN_USE = ("De vorige demonstratie kon niet worden verwijderd: een bestand in de map demo is nog geopend, bijvoorbeeld in Excel. "
               "Sluit dat bestand en dubbelklik opnieuw op Demonstratie starten.")


def test_a_demonstration_start_while_a_file_of_the_previous_one_is_open_says_to_close_it(tmp_path, quick, monkeypatch, windowless):
    """A sample of demo\\voorbeelden left open in Excel during a rehearsal: Windows keeps it locked, so the next start cannot empty
    the folder. Its warning box says which kind of file stops it and what to do, the log names the file, the marker stays and nothing
    is filled or started; once the file is closed the next start begins afresh."""
    windowless()
    demo = demo_folder(tmp_path)
    started_demo(demo)
    locked = demo / "voorbeelden"
    remove = portable._remove

    raised = PermissionError(13, "The process cannot access the file because it is being used by another process",
                             str(locked / "Verzonnen prijslijst S04.xlsx"))

    def in_use(path):
        if path == locked:
            raise raised
        remove(path)
    monkeypatch.setattr(portable, "_remove", in_use)
    boxes, folders = Boxes(), Folders({("data", "--status"): 1, ("demo", "--status"): 1})
    assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("information", DEMO_STARTING_BOX), ("closed", DEMO_STARTING_BOX), ("warning", DEMO_IN_USE)]
    assert ("fill",) not in folders.asked() and ("demo", *DEMO_LAUNCH) not in folders.asked()
    assert (demo / "DEMONSTRATIE.txt").is_file() and locked.is_dir()
    assert log(demo)[-1] == (f"demonstration start: the previous demonstration could not be removed (PermissionError: {raised}): "
                             "a file in the folder demo is still open, for example in Excel; nothing started")
    monkeypatch.setattr(portable, "_remove", remove)
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): [1, 0], ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30,
                               box=Boxes()) == 0
    assert (locked / "Verzonnen prijslijst S04.xlsx").is_file()


def test_a_demonstration_start_while_the_previous_launcher_is_still_stopping_removes_nothing(tmp_path, quick, windowless):
    """A double click on Demonstratie starten right after Demonstratie stoppen, while the old launcher still runs with STOP beside
    its settings: nothing is removed under the live launcher and nothing is started; one warning box says that it is still
    stopping."""
    windowless()
    demo = demo_folder(tmp_path)
    started_demo(demo)
    (demo / "STOP").write_text("", encoding="utf-8")
    (demo / "start_state.json").write_text("{}", encoding="utf-8")
    before = {name: data for name, data in files_of(demo).items() if name != "logs/start.log"}
    boxes, folders = Boxes(), Folders({("demo", "--status"): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(True), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [("demo", "--status")]
    assert boxes.events == [("warning", portable.DEMO_STILL_STOPPING)]
    assert {name: data for name, data in files_of(demo).items() if name != "logs/start.log"} == before
    assert log(demo)[-1] == ("demonstration start: the launcher on the demonstration's settings is still stopping (STOP exists): "
                             "nothing removed and nothing started")


def test_each_start_ends_while_the_other_start_holds_its_lock(tmp_path, quick, windowless):
    """Inkoopassistent starten and Demonstratie starten double-clicked close together: while the one start holds its start lock
    (younger than its limit), the other says in a warning box that the other runs and starts nothing, before it removes or fills
    anything; a lock left over by a start that did not end is passed over."""
    windowless()
    demo = demo_folder(tmp_path)
    data = demo.parent / "data"
    (data / "start.lock").write_text(json.dumps({"time": time.time(), "pid": 4242}), encoding="utf-8")
    data_before = files_of(data)
    boxes, folders = Boxes(), Folders({("data", "--status"): 1})
    assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [] and boxes.events[-1] == ("warning", REAL_RUNS)
    assert log(demo)[-1].startswith("demonstration start: a start of the aid of this folder is busy (start.lock of ")
    assert not (demo / "settings.json").exists() and files_of(data) == data_before
    # the other way round
    (data / "start.lock").unlink()
    (demo / "start.lock").write_text(json.dumps({"time": time.time(), "pid": 4243}), encoding="utf-8")
    boxes, folders = Boxes(), Folders({("data", "--status"): 1})
    assert portable.start(data, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert folders.asked() == [("data", "--status")] and boxes.events[-1] == ("warning", DEMO_RUNS)
    assert log(data)[-1].startswith("folder start: a start of the demonstration of this folder is busy (start.lock of ")
    # a lock left over long ago counts for nothing
    (demo / "start.lock").write_text(json.dumps({"time": 1000.0, "pid": 4243}), encoding="utf-8")
    folders = Folders({("data", "--status"): [1, 0], ("data", "--install-autostart"): 0})
    assert portable.start(data, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30, box=Boxes()) == 0


def test_the_clearing_never_walks_through_a_link_that_is_not_a_symlink(tmp_path, quick, monkeypatch):
    """A folder demo\\logs that is a link Windows does not call a symlink (a directory junction, made by a backup or sync tool):
    the next start removes the link and never a file in the folder it points to."""
    demo = demo_folder(tmp_path)
    started_demo(demo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("the owner's own", encoding="utf-8")
    shutil.rmtree(demo / "logs")
    (demo / "logs").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)              # as for a junction on Windows
    monkeypatch.setattr(os.path, "islink", lambda path: False)
    folders = Folders({("data", "--status"): 1, ("demo", "--status"): [1, 0], ("demo", *DEMO_LAUNCH): 0})
    assert portable.demo_start(demo, run=folders, answers=Page(False, False, True), open_page=Browser(), wait_seconds=30,
                               box=Boxes()) == 0
    assert (elsewhere / "precious.txt").read_text(encoding="utf-8") == "the owner's own"
    assert not os.path.realpath(demo / "logs").startswith(os.path.realpath(elsewhere))


@pytest.mark.parametrize("target_marked", [False, True])
def test_a_folder_demo_that_is_a_link_is_refused_and_never_followed(target_marked, tmp_path, quick, windowless):
    """A folder demo that is a link (a symlink or a junction) is refused as one the demonstration start did not make, whether what
    it points to does not exist or carries the marker: nothing is made, filled or asked at the link's target."""
    windowless()
    demo = demo_folder(tmp_path)
    target = tmp_path / "elders" / "demo"
    target.parent.mkdir()
    if target_marked:
        target.mkdir()
        (target / "DEMONSTRATIE.txt").write_text(portable.DEMO_MARKER_TEXT, encoding="utf-8")
    demo.symlink_to(target, target_is_directory=True)
    before = files_of(target.parent)
    boxes, folders = Boxes(), Folders({})
    assert portable.demo_start(demo, run=folders, answers=Page(False), open_page=Browser(), wait_seconds=30, box=boxes) == 1
    assert boxes.events == [("warning", DEMO_FOREIGN)] and folders.calls == []
    assert files_of(target.parent) == before and target.exists() == target_marked
    assert portable.demo_stop(demo, run=folders) == 1 and folders.calls == []


def test_a_folder_demo_made_a_moment_ago_by_another_start_is_not_called_foreign(tmp_path, monkeypatch):
    """Two double clicks on a folder that never had a demonstration: the second start can find the folder demo the first just
    made, before its marker is written. It waits a moment for the marker; an empty folder demo that gets none stays refused."""
    demo = demo_folder(tmp_path)
    demo.mkdir()
    writer = threading.Timer(0.3, lambda: (demo / "DEMONSTRATIE.txt").write_bytes(portable.DEMO_MARKER_TEXT.encode("ascii")))
    writer.start()
    try:
        assert portable._claimed(demo) is True
    finally:
        writer.join()
    other = tmp_path / "other" / "demo"
    other.mkdir(parents=True)
    monkeypatch.setattr(portable, "DEMO_MARKER_WAIT", 0.1)
    assert portable._claimed(other) is False
