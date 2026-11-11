"""The aid from a folder (aid/portable.py): the build of the folder with a fake download of the embeddable Python and a fake pip,
the start files, the first start, and the start, the stop and the end of the start at logon with a fake run of the launcher. No
test reaches the network, runs pip or reaches Windows, on any PC: the Windows calls are the fakes of these tests, and the fixture
no_real_build makes the PC look like no Windows to a build with its defaults, whose real download and real pip fail the test."""
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
FOLDER = sorted([*CMD_FILES, "LEESMIJ.txt", "python", "app"])
ORIGINAL_PTH = b"python311.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"
LEESMIJ = ("Deze map is de inkoopassistent. Buiten deze map zet de assistent alleen een snelkoppeling in de map Opstarten en, als u "
           "zich aanmeldt bij Microsoft, de aanmelding in uw eigen profiel. Starten: dubbelklik op Inkoopassistent "
           "starten; na ongeveer een halve minuut opent de pagina in uw browser, en daarna start de assistent vanzelf wanneer u zich op "
           "deze pc aanmeldt. Stoppen: dubbelklik op Inkoopassistent stoppen; de assistent blijft uit tot u hem weer start. In de map "
           "data staan de instellingen en alle documenten die de assistent verwerkt: kopieer of verstuur die map niet. Zet deze map op "
           "de eigen schijf van de pc, niet in een map die met OneDrive wordt gesynchroniseerd; verplaatst u de map, dubbelklik dan "
           "opnieuw op Inkoopassistent starten. Bijwerken: stop de assistent, vervang de mappen python en app door die van de nieuwe "
           "versie en start opnieuw; de map data blijft staan. Weggooien: meld u op de pagina af bij Microsoft, dubbelklik op "
           "Inkoopassistent niet meer vanzelf starten, stop de assistent en verwijder deze map.")
STARTING = "De inkoopassistent start. Even geduld: de pagina opent vanzelf in uw browser."
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
    def __init__(self, works: bool = True):
        self.works, self.opened = works, []

    def __call__(self, url: str) -> bool:
        self.opened.append(url)
        return self.works


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
    assert sorted(files) == sorted([*CMD_FILES, "LEESMIJ.txt"])
    for name, option in CMD_FILES.items():
        text = files[name]
        line = text.removesuffix("\r\n")
        assert "\n" not in line and "\r" not in line and text.isascii(), name                        # one line
        # -I: the user's own site-packages and Python's environment variables never reach the folder's Python
        assert line.startswith(f'@"%~dp0python\\python.exe" -I -X utf8 -m aid.portable {option} "%~dp0data"'), name
        assert not re.search(r"[A-Za-z]:[\\/]|\\\\|(^|\s)/", line), name                              # no drive, no network path
    # the start window stays open after a failure only; the stop and the end of the start at logon wait for a key
    assert files["Inkoopassistent starten.cmd"].endswith(" || pause\r\n")
    assert files["Inkoopassistent stoppen.cmd"].endswith(" & pause\r\n")
    assert files["Inkoopassistent niet meer vanzelf starten.cmd"].endswith(" & pause\r\n")
    readme = files["LEESMIJ.txt"]
    assert " ".join(readme.split()) == LEESMIJ and readme.isascii() and "\r\n" in readme and "\n" not in readme.replace("\r\n", "")


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
    (out / "app" / "aid" / "left_from_the_first_build.py").write_text("", encoding="utf-8")
    data_before = files_of(out / "data")
    assert windows_build(out, run=Pip(package="other_package")) == 0
    assert files_of(out / "data") == data_before and (out / "data" / "cases" / "250101_20261110T090000").is_dir()
    assert (site / "other_package").is_dir() and not (site / "made_up_package").exists()
    assert not (out / "app" / "aid" / "left_from_the_first_build.py").exists() and (out / "app" / "aid" / "portable.py").is_file()
    assert sorted(path.name for path in out.iterdir()) == sorted([*FOLDER, "data"])
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
    fetch = Fetch(data=embeddable_zip(sys.version_info[:2]))
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
               "madeup/out/MU01_conf.pdf", "madeup/tryout/EXPORT_nl.csv", "extension/manifest.json", "readers/templates/table_nl.json",
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
    comes up: the start asks --status for up to 90 seconds, and then waits up to 180 seconds for the page."""
    assert portable.LAUNCHER_WAIT == 90 and inspect.signature(portable.start).parameters["wait_seconds"].default == 180


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
    for name in ("build", "start", "stop", "no_autostart"):
        monkeypatch.setattr(portable, name, lambda *args, name=name, **kwargs: seen.append((name, args, kwargs)) or 0)
    assert portable.main(["--build", str(tmp_path / "out"), "--python-zip", str(tmp_path / "python.zip")]) == 0
    assert portable.main(["--start", str(tmp_path / "data")]) == 0
    assert portable.main(["--stop", str(tmp_path / "data")]) == 0
    assert portable.main(["--no-autostart", str(tmp_path / "data")]) == 0
    assert seen == [("build", (tmp_path / "out",), {"python_zip": tmp_path / "python.zip"}), ("start", (tmp_path / "data",), {}),
                    ("stop", (tmp_path / "data",), {}), ("no_autostart", (tmp_path / "data",), {})]
    with pytest.raises(SystemExit):
        portable.main(["--start", str(tmp_path / "data"), "--stop", str(tmp_path / "data")])


def test_the_module_imports_with_the_standard_library_only():
    """The start must reach its self-check, and the build needs none of the aid's packages: aid.portable imports nothing else."""
    script = ("import sys\nfor name in ('pydantic', 'gradio', 'PIL', 'msal', 'anthropic', 'requests'):\n    sys.modules[name] = None\n"
              "import aid.portable\nprint('ok')\n")
    done = subprocess.run([sys.executable, "-B", "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout == "ok\n", done.stderr
    assert dict(portable.ADDRESSES) == dict(__import__("aid.start").start.ADDRESSES)
