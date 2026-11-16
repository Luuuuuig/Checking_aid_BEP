"""The aid from a folder: built once on a Windows PC, carried as a folder to the PC where it runs, and started there by a double click
and at each logon, with nothing installed.

    python -m aid.portable --build <out> [--python-zip <file>]   on a Windows PC with a 64-bit Intel or AMD processor, with the
                                                                 Python that will run the aid
    python -m aid.portable --start <data folder>                  run by "Inkoopassistent starten.cmd" with the folder's pythonw.exe
    python -m aid.portable --stop <data folder>                   run by "Inkoopassistent stoppen.cmd"
    python -m aid.portable --no-autostart <data folder>           run by "Inkoopassistent niet meer vanzelf starten.cmd"

The folder that --build makes holds the program only:

    <out>/Inkoopassistent starten.cmd                     one line: start "" and the pythonw.exe of the folder python beside it,
                                                          through %~dp0 and in quotes, with -I -X utf8 -m aid.portable --start and
                                                          the folder data beside it; it ends at once, so that no window stays open
    <out>/Inkoopassistent stoppen.cmd                     one line: the python.exe of the folder python, in the same way, with
                                                          --stop, in a window that waits for a key
    <out>/Inkoopassistent niet meer vanzelf starten.cmd   the same with --no-autostart
    <out>/LEESMIJ.txt                                     for the buyer: what the folder is, how a zip of it from the internet is
                                                          unblocked, and how it is started, stopped, updated and removed
    <out>/python/                                         the embeddable Python of python.org, with Lib/site-packages holding the
                                                          packages
    <out>/app/aid/                                        the program: the files of the package aid that git tracks, without its
                                                          tests (outside git an allow-list of them)
    <out>/data/                                           made at the first start, never by the build: settings.json, inbox/,
                                                          cases/, po_store/, logs/, and start.lock while a start runs

The build (build) runs on Windows on a 64-bit Intel or AMD PC only, with a 64-bit Python: elsewhere it builds nothing, says in one
line where to run it and ends with exit code 2. It reads the system, the machine and the version when it is called. It takes
python.org's embeddable package of exactly the running Python version, fetched over https from www.python.org, or the file of
--python-zip, which may be the package of another release of the same minor version: a file with the name python.org gives it is
checked and named by the version in that name. For a version in EMBED_SHA256 the build checks the package's sha256 against the one
python.org's release page gives, and builds nothing when it differs; for another version (3.11 among them) it checks nothing itself,
and prints the sha256 and the md5 of the package with the release page, for the builder to compare before the folder is used (a
release page gives a SHA-256 from 3.13.12 and 3.14.3 on, an MD5 before). python.org gives no Windows files for the releases after
3.11.9 and after 3.12.10 (and after 3.13.17): on a 404 the build says so and names a version that has one, of the same minor
version, for a Python of python.org or for --python-zip. pip is called once, with the running interpreter, into
python/Lib/site-packages, with aid/requirements.txt without the line of the development AI client's library (requirements); since
this Python and this Windows resolve the packages, every package that Windows and this Python version need is installed. A failing
pip ends the build with pip's exit code. pip compiles nothing (a compiled file holds the folder it was compiled in), and the console
scripts it puts beside the packages, which hold the path of the builder's Python and are not used, are removed: no file of the
folder holds an absolute path or the value of a setting.
The ._pth file (pth_text) gives the embeddable Python its library, its own folder, Lib/site-packages and the folder app, and runs
import site. A ._pth file alone does not keep the user's own site-packages and Python's environment variables out (CPython applies
it after it has read them), so every Python the folder starts runs with -I: the start files, the launcher commands below, and the
launcher's parts and its start at logon (aid/start.py gives them -I when the launcher itself runs isolated).

The folder is made in a folder of its own beside <out> and moved into <out> at the end, so that a build that fails leaves nothing
behind, and an earlier build as it was. A second build into the same folder replaces python and app, writes the start files and
LEESMIJ.txt anew and leaves data as it is; while the aid runs from the folder, Windows keeps python in use, and the build says so and
changes nothing. An out folder that holds anything but an earlier build is refused, and so is one inside the package. app/aid holds
the program only (_program_files): in a git repository the files git tracks under aid/, else an allow-list (the package's .py
files, the files the code reads: the readers' templates and prompts, the messaging templates, madeup/out, madeup/tryout, extension,
README.md, MANUAL.md, requirements.txt and settings.example.json), and never the tests, a cache folder or a hidden file. Every other
file found under aid/ (evaluation results, a run's files, a second settings file) is left out and named in one line of the build's
output. Exit codes of the build: 0 built; 1 the embeddable package could not be had or used (the download, the file, its sha256,
its content), the package could not be copied, or python and app could not be replaced; 2 refused (the system, the out folder);
pip's own exit code when pip fails.

The start (start) runs with the folder's own pythonw.exe, which has no console (_console), so it tells the buyer what it does in
message boxes of Windows with the title TITLE (box: user32's MessageBoxW, BOX, unless start is given others). It first makes sure
that it can write into the data folder and its logs (a folder under C:\\Program Files or on a read-only share cannot be
written by a user): when it cannot, a warning box says where to put the folder, and nothing is started. Then it takes the start
lock, data/start.lock, made with exclusive creation and holding the time and the process id, which it removes at its end while it
is still its own. A start that finds a lock younger than LAUNCHER_WAIT, the wait for the page and LOCK_MARGIN together (seven and a
half minutes) is a second double click during a slow first start: it writes one line in start.log and ends with exit code 0 at
once, showing nothing, and the first start opens the page or shows the failure. An older lock is left by a start that was ended
before it could remove it (a logoff, a restart, Task Manager), and is taken over with a line. Until it is that old, a start after
such an end ends at once in the same way, also while a launcher runs (the lock is judged before --status): LEESMIJ.txt and section
7 of the manual tell the buyer to double-click again later. A lock that cannot be read yet is judged by the time of its file.
first_start writes data/settings.json when it is missing, with the test mode on for the owner's test, and never overwrites one.
Then the launcher's --status (aid/start.py) says whether a launcher runs on these settings: when one runs and no STOP
file stands beside the settings, only the page is opened, and no box is shown. Otherwise the start really starts, and one
information box says so (STARTING_BOX: STARTING, and that the first time can take a few minutes): it is shown from a daemon thread
of its own, so that the start goes on while it stands and it never keeps the process alive, and the start closes it itself (WM_CLOSE
to the dialog with the title TITLE that this thread made, tried again until the thread has ended, at most BOX_WAIT seconds) before
it opens the page or shows a failure, and at its end in any case. Then, in this order: when something answers at either of the
launcher's two addresses (ADDRESSES, the launcher's own) while no launcher runs on these settings (another aid on this PC, a part
started by hand), nothing is started; the self-check (self_check) imports the modules the parts need, and each module that cannot
be imported is named in data/logs/start.log with the class of its error and the first line of its message, and nothing is started;
the launcher's --install-autostart puts the shortcut in the Startup folder and starts the launcher in the background; when the STOP
file still stands beside the settings (--install-autostart starts nothing while it exists, and ends with exit code 0 all the same),
--resume --background deletes it and starts the launcher, after waiting for a launcher that is still stopping. --install-autostart
ends with 0 also when it started nothing now (an address that answers, a start in the background that failed), so the start then
asks --status, at most LAUNCHER_WAIT seconds (90: a slow first start is no failure), whether a launcher now runs: when none does in
the end, the assistant could not start. Then it waits until http://127.0.0.1:7860/ answers, at most wait_seconds (180), closes the
information box and opens the page in the default browser. A start that fails closes the information box and, once its lock is
removed (so that a double click while the box stands starts again), shows one warning box with its Dutch line, which waits for OK;
a fault of the start that main catches shows the box of a start that failed. That the start still waits for the launcher
(STARTING_UP) is no box: the information box says it already.
With a console (a start file of an earlier version runs python.exe in a window; or python.exe is started by hand) the start prints
these lines in the window, STARTING_UP among them and ALREADY_RUNNING when only the page is opened, as earlier versions did, and
shows no box. The stop (stop) runs the launcher's --stop: the STOP file keeps the aid off, also at the next logon, until the start
file is used again; it says that the assistant has stopped only when the launcher's state file is gone, and else that it is still
stopping. --no-autostart (no_autostart) runs the launcher's --remove-autostart.

Each launcher command is started with -I -X utf8 and the Python of the command (pythonw.exe for the start: a program without a
console, which Windows starts without a window), its output captured: the buyer sees the Dutch lines only, and
data/logs/start.log gets one line per command with its exit code and its output, beside the launcher's own lines (of the asks of
--status after the launcher's start, the last one). The launcher starts its own parts and PowerShell without a window as well
(aid/start.py). Every call that reaches Windows (the launcher's commands, the two addresses, the browser, the message boxes) is
a parameter. One that fails gives one Dutch line, in a box or in the window, and one line in start.log; a message box that
cannot be shown, or an information box that cannot be closed, gives its line in start.log only ("the information box could not
be shown", "... could not be closed", "the warning box could not be shown"), as the box was to carry the Dutch line. The windows
of the stop and of the end of the start at logon always wait for a key ("& pause"), and so does the window of a start file of an
earlier version after a failure ("|| pause"), so that their last line is read. Exit codes of the start, the stop and
--no-autostart: 0 done (the page opened, or another start still busy; the stop made; the start at logon removed), 1 otherwise; 2
a command line that is refused; 130 a Ctrl-C.

This module imports the standard library only, and ctypes only when it shows a message box on Windows: the build needs none of the
aid's packages, the start reaches its self-check when one of them is missing from the folder, and the module imports on any system.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import warnings
import webbrowser
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

PACKAGE = Path(__file__).resolve().parent        # the package aid, which the build copies into the folder app
DEVELOPMENT_CLIENT = "anthropic"                 # the library of the development AI client: the folder does without it
SETTINGS_FILE = "settings.json"
STOP_FILE = "STOP"                               # the stop switch of the launcher and the runner, beside the settings (aid/start.py)
STATE_FILE = "start_state.json"                  # the launcher's state file, beside the settings while it runs (aid/start.py)
PAGE = "http://127.0.0.1:7860/"                  # the review page, where the launcher has it (aid/start.py)
# The launcher's two fixed addresses (ADDRESSES of aid/start.py, which this module does not import): while one of them answers, the
# launcher starts nothing.
ADDRESSES = {"review": PAGE, "capture": "http://127.0.0.1:7870/health"}
WAIT_STEP = 1.0                                  # seconds between two asks of an address, or of the launcher's --status
LAUNCHER_WAIT = 90.0                             # seconds a launcher started in the background gets to run, as --status says
CHECK_TIMEOUT = 2.0                              # seconds an address gets to answer
LOCK_FILE = "start.lock"                         # in the data folder while a start runs: a second start meanwhile ends at once
# Seconds beyond LAUNCHER_WAIT and the wait for the page after which a start lock counts as left over: for what runs before those
# waits (the self-check's imports on a slow first start, the launcher's --status and --install-autostart, PowerShell).
LOCK_MARGIN = 180.0
TITLE = "Inkoopassistent"                        # the title of the message boxes of a start without a console
BOX_WAIT = 5.0                                   # seconds the start gives its information box to appear, and to be closed
BOX_STEP = 0.05                                  # seconds between two tries to close it
# The values of user32 (winuser.h): the icons and the flags of a message box, the message that closes a window, and the window
# class of a dialog, which a message box is.
MB_ICONWARNING, MB_ICONINFORMATION, MB_SETFOREGROUND, MB_TOPMOST = 0x30, 0x40, 0x10000, 0x40000
WM_CLOSE = 0x0010
DIALOG_CLASS = "#32770"
DOWNLOAD_TIMEOUT = 120.0
# The last version of each minor version for which python.org gives Windows files (its downloads API, read on 10 November 2026):
# the later releases of 3.11, 3.12 and 3.13 are source code only.
LAST_WINDOWS_PACKAGE = {(3, 11): (3, 11, 9), (3, 12): (3, 12, 10), (3, 13): (3, 13, 17)}
EMBED_NAME = re.compile(r"python-(\d+)\.(\d+)\.(\d+)-embed-amd64\.zip", re.IGNORECASE)    # the name python.org gives the package
START_CMD = "Inkoopassistent starten.cmd"
STOP_CMD = "Inkoopassistent stoppen.cmd"
NO_AUTOSTART_CMD = "Inkoopassistent niet meer vanzelf starten.cmd"
README = "LEESMIJ.txt"
PROGRAM = ("python", "app")                      # the folders of the program, which a second build replaces
DATA = "data"                                    # the folder of the settings and of every document, made at the first start
DATA_FOLDERS = ("inbox", "cases", "po_store", "logs")
# The modules the self-check imports: the three parts and the launcher, the page library (gradio, through import_gradio of the
# review page), and what the made-up set and the price-list import need.
SELF_CHECK = ("aid.start", "aid.runner", "aid.capture_server", "aid.review.page", "gradio", "aid.madeup.generate", "aid.pricelist", "PIL")
# The allow-list of the program, without git: besides the .py files, the files the code reads, by folder and by name.
PROGRAM_FOLDERS = ("readers/templates/", "readers/prompts/", "messaging/templates/", "madeup/out/", "madeup/tryout/", "extension/")
PROGRAM_FILES = ("README.md", "MANUAL.md", "requirements.txt", "settings.example.json")
# The sha256 of the embeddable package (64-bit) of each version, as python.org's release page of the version gives it under
# "Windows embeddable package (64-bit)", read from the page beside each value on 10 November 2026. python.org gives a SHA-256
# there from 3.13.12 and 3.14.3 on; the release pages of 3.11, of 3.12 and of the earlier 3.13 and 3.14 versions give an MD5 only,
# so those versions are not here, and their build prints the sha256 and the md5 with the release page to compare them with.
EMBED_SHA256: dict[str, str] = {
    "3.13.12": "76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0",  # https://www.python.org/downloads/release/python-31312/
    "3.13.13": "8766a8775746235e23cf5aee5027ab1060bb981d93110577adcf3508aa0cbd55",  # https://www.python.org/downloads/release/python-31313/
    "3.13.14": "90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907",  # https://www.python.org/downloads/release/python-31314/
    "3.13.15": "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf",  # https://www.python.org/downloads/release/python-31315/
    "3.13.16": "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297",  # https://www.python.org/downloads/release/python-31316/
    "3.13.17": "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38",  # https://www.python.org/downloads/release/python-31317/
    "3.14.3": "ad4961a479dedbeb7c7d113253f8db1b1935586b73c27488712beec4f2c894e6",  # https://www.python.org/downloads/release/python-3143/
    "3.14.4": "cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9",  # https://www.python.org/downloads/release/python-3144/
    "3.14.5": "ba6bd811c4eedb19195cf275770ef127e893d63701e24152606e2cb76f6d876a",  # https://www.python.org/downloads/release/python-3145/
    "3.14.6": "df901e84a896ff1ee720ad03377e0c8d8c2244fda79808aeeaff6316df1cb75c",  # https://www.python.org/downloads/release/python-3146/
    "3.14.7": "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15",  # https://www.python.org/downloads/release/python-3147/
    "3.14.8": "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310",  # https://www.python.org/downloads/release/python-3148/
    "3.14.9": "3c01d7cfc666f746a6f064f60fb2156ab0ec464d3d738ab796371a46a9bb24a2",  # https://www.python.org/downloads/release/python-3149/
    "3.14.10": "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159",  # https://www.python.org/downloads/release/python-31410/
    "3.15.0": "a6999bcbcbe56f937159ef79c2630260eca99f65f7860bc5cd20430324079a44",  # https://www.python.org/downloads/release/python-3150/
    "3.15.1": "352e232f25b5120508f11a6dfc1bd9076d3e70c30977d552a63338838c2ae8fb",  # https://www.python.org/downloads/release/python-3151/
    "3.15.2": "d012f3aa29cf07436dae8aea1fec0fd195c2612a0b059b1f915ae0afb9ad1f62",  # https://www.python.org/downloads/release/python-3152/
}

# The lines of the start and stop windows, and of the start's message boxes, in Dutch, the buyer's language; the English of each
# line stands above it.
# The purchasing assistant is starting. One moment: the page opens in your browser by itself.
STARTING = "De inkoopassistent start. Even geduld: de pagina opent vanzelf in uw browser."
# The information box of a start without a console: STARTING, and: the first time this can take a few minutes.
STARTING_BOX = STARTING + " De eerste keer kan dat een paar minuten duren."
# The purchasing assistant is already running. The page is being opened.
ALREADY_RUNNING = "De inkoopassistent draait al. De pagina wordt geopend."
# The purchasing assistant could not start. The reason is in the folder data, in logs, in the file start.log.
NOT_STARTED = "De inkoopassistent kon niet starten. De reden staat in de map data, in logs, in het bestand start.log."
# The purchasing assistant cannot write in this folder. Put the folder where you may write, for example in your own Documents
# folder, or ask for write access to the folder data.
UNWRITABLE = ("De inkoopassistent kan in deze map niet schrijven. Zet de map op een plek waar u mag schrijven, bijvoorbeeld in uw eigen "
              "map Documenten, of vraag schrijfrechten op de map data.")
# One moment: the purchasing assistant is still starting up. The first time this can take a few minutes.
STARTING_UP = "Even geduld: de inkoopassistent wordt nog opgestart. De eerste keer kan dat een paar minuten duren."
# The page does not answer yet. Open http://127.0.0.1:7860 in your browser yourself in a minute.
NOT_YET = "De pagina antwoordt nog niet. Open over een minuut zelf http://127.0.0.1:7860 in uw browser."
# The page could not be opened by itself. Open http://127.0.0.1:7860 in your browser yourself.
NOT_OPENED = "De pagina kon niet vanzelf worden geopend. Open zelf http://127.0.0.1:7860 in uw browser."
# The purchasing assistant is stopping. This takes at most a minute and a half.
STOPPING = "De inkoopassistent stopt. Dit duurt hoogstens anderhalve minuut."
# The purchasing assistant has stopped and stays off until you double-click Inkoopassistent starten again.
STOPPED = "De inkoopassistent is gestopt en blijft uit tot u weer op Inkoopassistent starten dubbelklikt."
# The purchasing assistant is still stopping. Wait a minute before you move or delete the folder.
STILL_STOPPING = "De inkoopassistent stopt nog. Wacht een minuut voordat u de map verplaatst of verwijdert."
# The purchasing assistant could not stop. The reason is in the folder data, in logs, in the file start.log.
NOT_STOPPED = "De inkoopassistent kon niet stoppen. De reden staat in de map data, in logs, in het bestand start.log."
# The purchasing assistant no longer starts by itself when you log on.
NO_AUTOSTART = "De inkoopassistent start niet meer vanzelf wanneer u zich aanmeldt."
# Starting by itself could not be switched off. The reason is in the folder data, in logs, in the file start.log.
AUTOSTART_KEPT = "Het vanzelf starten kon niet worden uitgezet. De reden staat in de map data, in logs, in het bestand start.log."
# LEESMIJ.txt, a paragraph each, in ASCII (the build writes it so): this folder is the purchasing assistant, and outside it the
# assistant puts only a shortcut in the Startup folder and, when you sign in to Microsoft, the sign-in in your own profile; a zip
# file from the internet, also of a new version, is unblocked before it is unpacked (right-click it, Properties, tick Unblock, OK),
# or Windows can hold the assistant back, and when it was unpacked already: stop the assistant if it runs, delete what was
# unpacked, but never a folder data, unblock it and unpack it again; start (no window stays open: a message says that it starts and
# closes when the page opens after about half a minute, the first time a few minutes; then at each logon; a start that fails says
# in a message what to do; a second double click while it starts does nothing, and after a logoff or a restart during that start a
# double click does nothing for up to eight minutes after the first); stop; the folder data (do not copy or send it); where to put
# the folder (not in a folder that OneDrive synchronises; after a move, start again); update (delete everything but data, put
# everything that is in the folder of the new version in, the files to double-click too, and start again; data stays; the zip of a
# new version holds a folder Inkoopassistent: unpack it elsewhere first and put what is in that folder in, not the folder itself);
# remove (sign out of Microsoft on the page, no longer start at logon, stop, delete the folder).
LEESMIJ = ("Deze map is de inkoopassistent. Buiten deze map zet de assistent alleen een snelkoppeling in de map Opstarten en, als u "
           "zich aanmeldt bij Microsoft, de aanmelding in uw eigen profiel.",
           "Komt de assistent als zip-bestand van internet, ook een nieuwe versie, hef dan de blokkering op voordat u het uitpakt: klik "
           "met de rechtermuisknop op het zip-bestand, kies Eigenschappen, vink Blokkering opheffen aan en klik op OK. Anders kan "
           "Windows de assistent tegenhouden. Hebt u het al uitgepakt, stop dan de assistent als die draait en verwijder wat u "
           "uitpakte, maar nooit een map data; hef daarna de blokkering alsnog op en pak het zip-bestand opnieuw uit.",
           "Starten: dubbelklik op Inkoopassistent starten. Er blijft geen venster open: een melding zegt dat de assistent start, en na "
           "ongeveer een halve minuut sluit die en opent de pagina in uw browser (de eerste keer kan dat een paar minuten duren). "
           "Daarna start de assistent vanzelf wanneer u zich op deze pc aanmeldt. Lukt het starten niet, dan staat in een melding wat "
           "u kunt doen. Dubbelklikt u nog eens terwijl de assistent start, dan gebeurt er niets: de eerste start opent de pagina. "
           "Hebt u zich afgemeld of is de pc opnieuw opgestart terwijl de assistent startte, dan doet een dubbelklik tot acht minuten "
           "na de eerste dubbelklik niets: dubbelklik daarna opnieuw.",
           "Stoppen: dubbelklik op Inkoopassistent stoppen; de assistent blijft uit tot u hem weer start.",
           "In de map data staan de instellingen en alle documenten die de assistent verwerkt: kopieer of verstuur die map niet.",
           "Zet deze map op de eigen schijf van de pc, niet in een map die met OneDrive wordt gesynchroniseerd; verplaatst u de map, "
           "dubbelklik dan opnieuw op Inkoopassistent starten.",
           "Bijwerken: stop de assistent, verwijder in deze map alles behalve de map data, zet alles wat in de map van de nieuwe versie "
           "staat in deze map, ook de bestanden waarop u dubbelklikt, en start opnieuw; de map data blijft staan. Het zip-bestand van "
           "een nieuwe versie bevat een map Inkoopassistent: pak het eerst uit op een andere plek, bijvoorbeeld in Downloads, en zet "
           "wat in die map Inkoopassistent staat in deze map, niet die map zelf.",
           "Weggooien: meld u op de pagina af bij Microsoft, dubbelklik op Inkoopassistent niet meer vanzelf starten, stop de assistent "
           "en verwijder deze map.")


def _say(line: str) -> None:
    """A line in the window (under pythonw there is none)."""
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except (OSError, ValueError, UnicodeError):
            pass


class _Log:
    """Lines into <data folder>/logs/start.log with the time, as the launcher writes its own (aid/start.py). A log that cannot be
    written is passed over, and no data folder is made for it."""

    def __init__(self, data: Path):
        self.file = Path(data) / "logs" / "start.log"

    def __call__(self, line: str) -> None:
        try:
            self.file.parent.mkdir(exist_ok=True)
            with open(self.file, "a", encoding="utf-8") as log:
                log.write(f"{datetime.now().isoformat(sep=' ', timespec='seconds')} {line}\n")
        except OSError:
            pass


class _MessageBoxes:
    """The message boxes of Windows: MessageBoxW of user32, with the title TITLE, an information or a warning. A call shows a box
    and returns when the box is closed, by the buyer or by close. ctypes is imported at the first box, on Windows only, so that the
    module imports nothing it needs there alone; off Windows no box is shown. The tests give a user32 of their own."""

    def __init__(self, user32=None):
        self.user32 = user32

    def _windows(self):
        """user32, with the types of its four calls as Windows declares them; None off Windows."""
        if self.user32 is None and os.name == "nt":
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
            user32.MessageBoxW.restype = ctypes.c_int
            user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowExW.restype = wintypes.HWND         # as wide as a pointer: a 64-bit handle is not cut
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostMessageW.restype = wintypes.BOOL
            self.user32 = user32
        return self.user32

    def __call__(self, text: str, warning: bool) -> None:
        """Show a box and wait until it is closed. A warning stands above the other windows, since it can come minutes after the
        double click, while the buyer works in another window."""
        user32 = self._windows()
        if user32 is not None:
            user32.MessageBoxW(None, text, TITLE, (MB_ICONWARNING | MB_TOPMOST if warning else MB_ICONINFORMATION) | MB_SETFOREGROUND)

    def close(self, thread: int) -> bool:
        """Post WM_CLOSE to the box that the thread with this native id shows: a dialog with the title TITLE that this thread made,
        never another window of that title (the folder open in Explorer) or the box of another start. Whether there was one."""
        user32, window = self._windows(), None
        while user32 is not None:
            window = user32.FindWindowExW(None, window, DIALOG_CLASS, TITLE)
            if not window:
                break
            if user32.GetWindowThreadProcessId(window, None) == thread:
                return bool(user32.PostMessageW(window, WM_CLOSE, 0, 0))
        return False


BOX = _MessageBoxes()                            # the boxes of a start without a console, unless start is given others


def _console() -> bool:
    """Whether the start has a console to print its lines in: python.exe has; pythonw.exe, which the start file runs, has none, and
    sys.stdout is None there (or, had Windows handed it an output, no one would read it)."""
    return sys.stdout is not None and Path(sys.executable).stem.lower() != "pythonw"


class _Told:
    """What a start tells the buyer. With a console (a start file of an earlier version runs python.exe in a window, or python.exe
    is started by hand) the lines are printed, as _say prints them, and no box appears. Without one (pythonw.exe, which the start
    file runs: _console) box shows them: one information box while the start really starts, shown from a daemon thread of
    its own, so that the start goes on while it stands and it never keeps the process alive, and closed by the start itself; and
    for a failure one warning box, which waits for OK, shown by warn once the start has removed its lock. A box that cannot be
    shown or closed changes nothing of the start, and log (the start's start.log, named as who) gets one line for it: without a
    console that line is all that is left of what the buyer was to read."""

    def __init__(self, box, log: Callable[[str], None] = lambda line: None, who: str = "folder start"):
        self.box = None if _console() else box               # None: a console
        self.log, self.who = log, who
        self.thread: threading.Thread | None = None          # the information box's thread, while it may stand
        self.native: int | None = None                       # that thread's native id, by which its box is found
        self.asking = threading.Event()                      # set by that thread just before it asks for the box
        self.failure: str | None = None                      # the line of a failure, for warn

    def starting(self) -> None:
        """STARTING in the window, or the information box."""
        if self.box is None:
            _say(STARTING)
            return
        self.thread = threading.Thread(target=self._inform, name="information box", daemon=True)
        self.thread.start()
        self.asking.wait(BOX_WAIT)

    def _inform(self) -> None:
        self.native = threading.get_native_id()
        self.asking.set()
        try:
            self.box(STARTING_BOX, False)
        except Exception as exc:
            self.log(f"{self.who}: the information box could not be shown ({type(exc).__name__}: {exc})")

    def close(self) -> None:
        """Close the information box, if it stands, and wait until it is gone, at most BOX_WAIT seconds: Windows makes the window of
        a box a moment after it is asked for, so the close is tried again until the box's thread has ended. A close that raises is
        not tried again, and a box still standing at the end of the wait stands until the start ends, as the start's process takes
        its windows along."""
        thread, self.thread = self.thread, None
        if thread is None or self.native is None:
            return
        deadline = time.monotonic() + BOX_WAIT
        while thread.is_alive() and time.monotonic() < deadline:
            try:
                self.box.close(self.native)
            except Exception as exc:
                self.log(f"{self.who}: the information box could not be closed ({type(exc).__name__}: {exc})")
                return
            thread.join(BOX_STEP)
        if thread.is_alive():
            self.log(f"{self.who}: the information box could not be closed: it still stood after {BOX_WAIT:g} seconds")

    def failed(self, line: str) -> None:
        """The Dutch line of a failure: in the window; without one, the information box is closed and the line kept for warn."""
        if self.box is None:
            _say(line)
            return
        self.close()
        self.failure = line

    def warn(self) -> None:
        """The warning box of the failure, if there was one: it waits for OK."""
        line, self.failure = self.failure, None
        if line is not None:
            try:
                self.box(line, True)
            except Exception as exc:
                self.log(f"{self.who}: the warning box could not be shown ({type(exc).__name__}: {exc})")


# --- the folder's files ---

def embed_url(version: tuple[int, int, int]) -> str:
    """The address of python.org's embeddable package (64-bit) of the version."""
    dotted = ".".join(str(part) for part in version)
    return f"https://www.python.org/ftp/python/{dotted}/python-{dotted}-embed-amd64.zip"


def release_page(version: tuple[int, int, int]) -> str:
    """The release page of the version on python.org, which gives the checksum of each of its files."""
    return f"https://www.python.org/downloads/release/python-{''.join(str(part) for part in version)}/"


def pth_text(version: tuple[int, int]) -> str:
    """The five lines of python3XX._pth, with the line ends of Windows: the library zip, the folder python itself, Lib\\site-packages,
    the folder app beside the folder python, and import site, so that a package's own .pth file is honoured."""
    return "\r\n".join([f"python{version[0]}{version[1]}.zip", ".", "Lib\\site-packages", "..\\app", "import site"]) + "\r\n"


def start_files() -> dict[str, str]:
    """File name -> text of the three .cmd files and of LEESMIJ.txt. Each .cmd file is one line that calls a Python of the folder
    python beside it, with -I (see the module docstring), with the folder data beside it, both through %~dp0 (the file's own folder,
    whatever it is) and in quotes. The start file starts pythonw.exe, which has no console, through start, and ends at once, so that
    no window stays open: the start tells the buyer what it does in message boxes. The stop and the end of the start at logon run
    python.exe in their window, which waits for a key, so that their last line is read."""
    def line(python: str, option: str) -> str:
        return f'"%~dp0python\\{python}" -I -X utf8 -m aid.portable {option} "%~dp0{DATA}"'
    return {START_CMD: f'@start "" {line("pythonw.exe", "--start")}\r\n', STOP_CMD: f'@{line("python.exe", "--stop")} & pause\r\n',
            NO_AUTOSTART_CMD: f'@{line("python.exe", "--no-autostart")} & pause\r\n', README: "\r\n\r\n".join(LEESMIJ) + "\r\n"}


def requirements(text: str) -> str:
    """aid/requirements.txt without the line of the development AI client's library; every other line as it is."""
    return "".join(line for line in text.splitlines(keepends=True) if _requirement(line) != DEVELOPMENT_CLIENT)


def _requirement(line: str) -> str | None:
    """The package a requirement line names, in the normal form of its name; None for a comment or an empty line."""
    found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
    return re.sub(r"[-_.]+", "-", found.group(1)).lower() if found is not None else None


# --- the build ---

def _download(url: str) -> bytes:
    """The bytes at an address of www.python.org, over https."""
    if not url.startswith("https://www.python.org/"):
        raise ValueError(f"{url} is no address of https://www.python.org/")
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as answer:
        return answer.read()


_run = subprocess.run                            # how pip is run when the build is given no run


def _tracked(folder: Path) -> list[str] | None:
    """The files git tracks under the folder, relative to it with forward slashes; None when the folder lies in no git repository
    or git cannot be run."""
    try:
        done = subprocess.run(["git", "-C", str(folder), "ls-files", "-z", "--", "."], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [name for name in done.stdout.decode("utf-8", errors="surrogateescape").split("\0") if name]


def _program_files() -> tuple[list[str], list[str]]:
    """The files of the package that the folder gets, and the other files found under it, which are left out and named; each relative
    to the package with forward slashes, sorted. The program is what git tracks, or, outside git, the allow-list (PROGRAM_FOLDERS,
    PROGRAM_FILES and the .py files); never the tests, a cache folder, a compiled file or a hidden file, which are left out without a
    word."""
    listed = _tracked(PACKAGE)
    tracked = set(listed) if listed is not None else None
    program, other = [], []
    for name in sorted(path.relative_to(PACKAGE).as_posix() for path in PACKAGE.rglob("*") if path.is_file()):
        parts = name.split("/")
        if parts[0] == "tests" or any(part.startswith(".") or part == "__pycache__" for part in parts) or name.endswith((".pyc", ".pyo")):
            continue
        if tracked is not None:
            wanted = name in tracked
        else:
            wanted = name.endswith(".py") or name in PROGRAM_FILES or name.startswith(PROGRAM_FOLDERS)
        (program if wanted else other).append(name)
    return program, other


def _refusal(out: Path) -> str | None:
    """Why the build refuses this out folder; None when it may build into it: a folder that does not exist, or that holds an
    earlier build only (names compared as Windows does, without regard to case)."""
    if out == PACKAGE or PACKAGE in out.parents:
        return f"{out} lies inside the package aid, which the build copies: choose a folder elsewhere"
    if not out.exists():
        return None
    if not out.is_dir():
        return f"{out} is a file: choose a folder"
    known = {name.lower() for name in (*start_files(), *PROGRAM, DATA)}
    other = sorted(path.name for path in out.iterdir() if path.name.lower() not in known)
    if other:
        return f"{out} holds other files than an earlier build of the folder ({', '.join(other)}): choose an empty or a new folder"
    return None


def build(out: Path, python_zip: Path | None = None, run=None, fetch=None, system: str | None = None, machine: str | None = None,
          version: tuple[int, int, int] | None = None) -> int:
    """Make the folder in out (see the module docstring); the exit code. The defaults are read when build is called: run is
    subprocess.run, fetch the download from python.org, and the system, the machine and the version are this PC's and this Python's."""
    run = run if run is not None else _run
    fetch = fetch if fetch is not None else _download
    system = system if system is not None else platform.system()
    machine = machine if machine is not None else platform.machine()
    version = tuple(version if version is not None else sys.version_info[:3])
    of = (_package_version(python_zip) if python_zip is not None else None) or version      # the version of the package
    if system != "Windows" or machine.upper() != "AMD64" or sys.maxsize <= 2 ** 32:
        _say("The folder is built on Windows on a 64-bit Intel or AMD PC, with the 64-bit Python that will run the aid: run "
             "python -m aid.portable --build <folder> there")
        return 2
    out = Path(out).resolve()
    refused = _refusal(out)
    if refused is not None:
        _say(refused)
        return 2
    name = embed_url(of).rpartition("/")[2]
    try:
        if python_zip is not None:
            package = Path(python_zip).read_bytes()
        else:
            _say(f"Downloading {embed_url(version)}")
            package = fetch(embed_url(version))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            _say(f"The embeddable package could not be had (HTTPError: {exc}): nothing was built")
            return 1
        _say(_no_windows_package(version))
        return 1
    except (OSError, ValueError) as exc:         # urllib's other errors are OSErrors
        _say(f"The embeddable package could not be had ({type(exc).__name__}: {exc}): nothing was built")
        return 1
    sha256 = hashlib.sha256(package).hexdigest()
    expected = EMBED_SHA256.get(".".join(str(part) for part in of))
    if expected is not None and sha256 != expected:
        _say(f"The sha256 of {name} is {sha256}, and python.org's release page gives {expected} ({release_page(of)}): "
             "nothing was built")
        return 1
    if expected is not None:
        _say(f"The sha256 of {name} matches the one of python.org's release page ({release_page(of)})")
    else:
        md5 = hashlib.md5(package, usedforsecurity=False).hexdigest()
        _say(f"This version is not in EMBED_SHA256: compare the sha256 {sha256} and the md5 {md5} of {name} with its release page "
             f"{release_page(of)}")
    missing = [folder for folder in (out.parent, *out.parent.parents) if not folder.exists()]
    first_made = missing[-1] if missing else None    # the highest folder above out that the build makes: a failed build removes it
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".aid-build-", dir=out.parent))     # short: Windows limits the length of a path
    code = 1
    try:
        code = _made(staging, package, name, version, run)
        if code == 0:
            code = _moved_in(staging, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if code != 0 and first_made is not None:
            shutil.rmtree(first_made, ignore_errors=True)
    if code == 0:
        files = [path for folder in PROGRAM for path in (out / folder).rglob("*") if path.is_file()]
        _say(f"Built {out}: {len(files)} files in {sum(path.stat().st_size for path in files) / 1e6:.0f} MB. The folder data is made "
             f"at the first start: double-click {START_CMD} on the PC where the aid runs")
    return code


def _package_version(path: Path) -> tuple[int, int, int] | None:
    """The version in the name of a file of --python-zip that has the name python.org gives the package (python-3.11.9-embed-amd64.zip,
    for a builder whose own version has no package); None for another name, which leaves the version of the running Python."""
    found = EMBED_NAME.fullmatch(Path(path).name)
    return tuple(int(part) for part in found.groups()) if found is not None else None


def _no_windows_package(version: tuple[int, int, int]) -> str:
    """What the build says when python.org has no Windows package for the version: which Python builds the folder instead."""
    minor = f"{version[0]}.{version[1]}"
    last = LAST_WINDOWS_PACKAGE.get(tuple(version[:2]))
    dotted = ".".join(str(part) for part in last) if last is not None else None
    return (f"python.org has no Windows package for Python {'.'.join(str(part) for part in version)}: build the folder with a Python of "
            f"python.org of a version that has one{f' (for {minor} the last is {dotted})' if last else ''}, or with --python-zip and "
            f"the embeddable package of such a version of Python {minor}"
            + (f" (python-{dotted}-embed-amd64.zip, from {release_page(last)})" if last else "") + ": nothing was built")


def _made(staging: Path, package: bytes, name: str, version: tuple, run) -> int:
    """The folders python and app in staging: the embeddable package unpacked, its ._pth file, the packages through pip, and the
    copy of the package aid."""
    python, tag = staging / "python", f"python{version[0]}{version[1]}"
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            if not {"python.exe", "pythonw.exe", f"{tag}.zip"} <= set(archive.namelist()):
                _say(f"{name} is not the embeddable package of this Python (python.exe, pythonw.exe and {tag}.zip are not all in it): "
                     "nothing was built")
                return 1
            archive.extractall(python)
    except (zipfile.BadZipFile, OSError) as exc:
        _say(f"{name} could not be unpacked ({type(exc).__name__}: {exc}): nothing was built")
        return 1
    (python / f"{tag}._pth").write_bytes(pth_text(version[:2]).encode("ascii"))
    site = python / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    wanted = staging / "requirements.txt"
    wanted.write_text(requirements((PACKAGE / "requirements.txt").read_text(encoding="utf-8")), encoding="utf-8")
    _say("Installing the packages with pip into python/Lib/site-packages; this takes a few minutes")
    try:
        done = run([sys.executable, "-m", "pip", "install", "--target", str(site), "--no-compile", "--disable-pip-version-check",
                    "-r", str(wanted)])
    except (OSError, subprocess.SubprocessError) as exc:
        _say(f"pip could not be run ({type(exc).__name__}: {exc}): nothing was built")
        return 1
    if done.returncode != 0:
        _say(f"pip ended with exit code {done.returncode}: nothing was built")
        return done.returncode
    shutil.rmtree(site / "bin", ignore_errors=True)          # pip's console scripts: they hold the path of the builder's Python
    program, other = _program_files()
    try:
        for name in program:
            target = staging / "app" / "aid" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PACKAGE / name, target)
    except OSError as exc:                                   # a file of the package that cannot be read
        _say(f"The package aid could not be copied ({type(exc).__name__}): nothing was built")
        return 1
    if other:
        _say(f"Left out of app/aid, as no part of the program: {', '.join(other)}")
    return 0


def _moved_in(staging: Path, out: Path) -> int:
    """Move python and app from staging into out, with the start files: the folders of an earlier build are moved aside into
    staging first, and every move is undone when one fails (Windows keeps the folder python in use while the aid runs from it)."""
    made = not out.exists()
    moved: list[tuple[Path, Path]] = []
    try:
        out.mkdir(exist_ok=True)
        for name in PROGRAM:
            if (out / name).exists():
                os.replace(out / name, staging / f"earlier {name}")
                moved.append((out / name, staging / f"earlier {name}"))
        for name in PROGRAM:
            os.replace(staging / name, out / name)
            moved.append((staging / name, out / name))
        for name, text in start_files().items():
            (out / name).write_bytes(text.encode("ascii"))
    except OSError as exc:
        for source, target in reversed(moved):
            try:
                os.replace(target, source)
            except OSError:
                pass
        if made:
            shutil.rmtree(out, ignore_errors=True)
        _say(f"python and app could not be replaced in {out} ({type(exc).__name__}: {exc}): when the aid runs from this folder, stop it "
             f"with {STOP_CMD} and build again")
        return 1
    return 0


# --- the first start, the self-check ---

def _token() -> str:
    return secrets.token_urlsafe(32)             # 43 characters, from 32 random bytes


def first_start(data: Path, token: Callable[[], str] = _token) -> Path:
    """Write data/settings.json when it is missing, and make the folders inbox, cases, po_store and logs beside it: the example
    settings with the parser as the reader, the folders by their names beside the file (so that the folder may be moved), a capture
    token of the token function (43 random characters), the example's addresses on example.com, and the test mode on, for the owner's
    test with the test account of Exact (the hand-over switches it off), with no own addresses of the test mode, so that the aid sends
    nothing to the example's addresses until the owner fills them in. A settings file that exists is never overwritten, whatever it
    holds. The path of the settings file."""
    data = Path(data)
    path = data / SETTINGS_FILE
    if path.exists():
        return path
    settings = json.loads((PACKAGE / "settings.example.json").read_text(encoding="utf-8"))
    settings.update({"reader": "parser", "cases_root": "cases", "po_store_root": "po_store", "watched_folder": "inbox",
                     "capture_token": token(), "test_mode": True})
    data.mkdir(parents=True, exist_ok=True)
    for name in DATA_FOLDERS:
        (data / name).mkdir(exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as file:     # exclusive: a file that appeared meanwhile is left as it is
            file.write(json.dumps(settings, indent=2) + "\n")
    except FileExistsError:
        pass
    return path


class _Missing(str):
    """The name of a module the self-check cannot import, a text like any other, with why in `reason`: the class of the error and
    the first line of its message (a missing package reads otherwise than a DLL that Windows blocks). No value of a document is
    involved."""
    reason = ""

    @classmethod
    def of(cls, name: str, error: BaseException) -> "_Missing":
        missing = cls(name)
        lines = str(error).splitlines()
        missing.reason = f"{type(error).__name__}: {lines[0]}" if lines else type(error).__name__
        return missing


def self_check() -> list[str]:
    """The modules of SELF_CHECK that cannot be imported with this Python, in that order, each with its reason (_Missing); [] when
    all can. The page library is imported as the review page imports it (import_gradio of aid/review/page.py), or, when the review
    page itself cannot be imported (it is named then), on its own, so that each module is named for its own reason. A warning a
    module gives while it is imported is kept from the buyer's window."""
    missing = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name in SELF_CHECK:
            try:
                if name == "gradio":
                    _import_gradio()
                else:
                    importlib.import_module(name)
            except Exception as error:           # not in the folder, or an error while it is imported (a DLL Windows refuses)
                missing.append(_Missing.of(name, error))
    return missing


def _import_gradio() -> None:
    """The page library, through the review page's import_gradio(); on its own when the review page cannot be imported."""
    try:
        page = importlib.import_module("aid.review.page")
    except Exception:                            # named by the self-check under aid.review.page
        importlib.import_module("gradio")
        return
    page.import_gradio()


# --- the start, the stop, and the end of the start at logon ---

def _answers(url: str) -> bool:
    """Whether the address answers with a status below 400, asked without any proxy of the environment."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=CHECK_TIMEOUT) as answer:
            return 200 <= answer.status < 400
    except Exception:                            # refused, timed out, an HTTP error: it does not answer
        return False


class _Ran(NamedTuple):
    code: int | None                             # the exit code; None when the command could not be run
    said: str                                    # its output on one line, or the error that kept it from running


def _launcher(run, data: Path, *options: str) -> _Ran:
    """Run a command of the launcher on the folder's settings with this Python, with -I (see the module docstring) and -X utf8, its
    output captured."""
    cmd = [sys.executable, "-I", "-X", "utf8", "-m", "aid.start", "--settings", str(data / SETTINGS_FILE), *options]
    try:
        done = run(cmd, cwd=str(PACKAGE.parent), stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return _Ran(None, f"{type(exc).__name__}: {exc}")
    lines = [line.strip() for line in f"{done.stdout or ''}\n{done.stderr or ''}".splitlines() if line.strip()]
    return _Ran(done.returncode, " | ".join(lines))


def _logged(who: str, options: tuple[str, ...], ran: _Ran) -> str:
    """The line of start.log of a launcher command."""
    what = " ".join(options)
    if ran.code is None:
        return f"{who}: {what} could not be run ({ran.said})"
    return f"{who}: {what} ended with exit code {ran.code}" + (f": {ran.said}" if ran.said else "")


def _writable(data: Path) -> bool:
    """Whether the data folder and its logs can be made and written: the start, the launcher and the parts write there."""
    try:
        (data / "logs").mkdir(parents=True, exist_ok=True)
        with open(data / "logs" / "start.log", "a", encoding="utf-8"):
            pass
    except OSError:
        return False
    return True


def _held(lock: Path) -> tuple[float, str] | None:
    """When the start lock was made, and how start.log names it; None when it is gone. The time is the one the lock holds, or, while
    it holds none that can be read (a start that writes it in this very moment), the time of the file."""
    try:
        made, text = lock.stat().st_mtime, lock.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    try:
        held = json.loads(text)
        stamp, pid = float(held["time"]), int(held["pid"])
        when = datetime.fromtimestamp(stamp).isoformat(sep=" ", timespec="seconds")
    except (ValueError, TypeError, KeyError, OverflowError, OSError):      # also a time that no date has
        return made, f"{LOCK_FILE} of {datetime.fromtimestamp(made).isoformat(sep=' ', timespec='seconds')}"
    return stamp, f"{LOCK_FILE} of {when}, process {pid}"


def _locked(data: Path, log: _Log, limit: float) -> str | None:
    """Take the start lock: data/start.lock, made with exclusive creation, holding the time and the process id. Its text, which the
    start gives _unlocked at its end; None when another start holds a lock younger than limit seconds: that start is still busy (a
    second double click during a slow first start), and start.log gets one line. An older lock is left by a start that was ended
    before it could remove it (a logoff, Task Manager): it is taken over, with a line. When no lock can be made at all, start.log
    says why and the start goes on without one ("")."""
    lock, text = data / LOCK_FILE, json.dumps({"time": time.time(), "pid": os.getpid()})
    why = "another start made it again each time"
    for _ in range(3):                           # a lock that went in the meantime, or one taken over, is made anew
        try:
            with open(lock, "x", encoding="utf-8") as file:
                file.write(text)
            return text
        except FileExistsError:
            pass
        except OSError as exc:
            why = f"{type(exc).__name__}: {exc}"
            break
        try:
            held = _held(lock)
            if held is None:
                continue
            made, named = held
            if abs(time.time() - made) < limit:
                log(f"folder start: another start is still busy ({named}): this one ends; that one opens the page or says why it "
                    "could not")
                return None
            log(f"folder start: the start lock ({named}) is older than {limit:g} seconds, left by a start that did not end: taken over")
            lock.unlink(missing_ok=True)
        except OSError as exc:
            why = f"{type(exc).__name__}: {exc}"
            break
    log(f"folder start: {LOCK_FILE} could not be made ({why}); the start goes on without it")
    return ""


def _unlocked(data: Path, text: str) -> None:
    """Remove the start lock while it holds this start's own text: one that another start took over, as older than the limit, is
    that start's."""
    lock = data / LOCK_FILE
    try:
        if text and lock.read_text(encoding="utf-8") == text:
            lock.unlink()
    except OSError:
        pass


def start(data: Path, run=subprocess.run, answers=_answers, open_page=webbrowser.open, wait_seconds: float = 180, box=None) -> int:
    """Start the aid of the folder and open its page (see the module docstring); 0 when the page was opened, or when another start
    is still busy, 1 otherwise. box shows the message boxes of a start without a console: Windows' own (BOX), read when start is
    called, unless it is given."""
    data = Path(data).resolve()
    log = _Log(data)                             # it never makes the data folder, so it may stand before the check of it
    told = _Told(box if box is not None else BOX, log)
    if not _writable(data):
        told.failed(UNWRITABLE)
        told.warn()
        return 1
    lock = _locked(data, log, LAUNCHER_WAIT + wait_seconds + LOCK_MARGIN)
    if lock is None:
        return 0
    try:
        code = _started(data, run, answers, open_page, wait_seconds, log, told)
    finally:
        told.close()
        _unlocked(data, lock)
    told.warn()                                  # once the lock is gone: a double click while the warning stands starts again
    return code


def _started(data: Path, run, answers, open_page, wait_seconds: float, log: _Log, told: _Told) -> int:
    """The start under its lock (see start)."""
    try:
        first_start(data)
    except OSError as exc:
        log(f"folder start: the settings file could not be written ({type(exc).__name__}: {exc})")
        told.failed(NOT_STARTED)
        return 1
    status = _launcher(run, data, "--status")
    log(_logged("folder start", ("--status",), status))
    if status.code == 0 and not (data / STOP_FILE).exists():
        _say(ALREADY_RUNNING)
        log("folder start: a launcher runs on these settings: only the page is opened")
        return _opened(log, answers, open_page, wait_seconds, told)
    told.starting()
    answering = [url for url in ADDRESSES.values() if answers(url)] if status.code != 0 else []
    if answering:
        log(f"folder start: something already answers at {' and '.join(answering)} while no launcher runs on these settings (another "
            "aid on this PC, or a part started by hand): only one aid runs on a PC; stop the other one and start again; nothing started")
        told.failed(NOT_STARTED)
        return 1
    missing = self_check()
    if missing:
        for name in missing:
            if getattr(name, "reason", ""):
                log(f"folder start: {name} cannot be imported ({name.reason})")
        log(f"folder start: these modules cannot be imported: {', '.join(missing)}; nothing started")
        told.failed(NOT_STARTED)
        return 1
    for options in (("--install-autostart",), ("--resume", "--background")):
        if options[0] == "--resume" and not (data / STOP_FILE).exists():
            break
        ran = _launcher(run, data, *options)
        log(_logged("folder start", options, ran))
        if ran.code != 0:
            told.failed(NOT_STARTED)
            return 1
    if not _launcher_runs(run, data, log):
        told.failed(NOT_STARTED)
        return 1
    return _opened(log, answers, open_page, wait_seconds, told)


def _launcher_runs(run, data: Path, log: _Log) -> bool:
    """Whether a launcher runs on the folder's settings after its start, as its --status says, asked every WAIT_STEP seconds for at
    most LAUNCHER_WAIT seconds (a launcher started in the background writes its state file a few seconds after its start; on a
    slow first start, while a virus scanner checks the new files, it can take a minute). When the first ask finds none yet, the
    window says once that it waits (STARTING_UP). The last ask gets its line in start.log, and a launcher that does not run one
    more line."""
    deadline, waiting = time.monotonic() + LAUNCHER_WAIT, False
    while True:
        status = _launcher(run, data, "--status")
        if status.code == 0 or time.monotonic() >= deadline:
            break
        if not waiting:
            _say(STARTING_UP)
            waiting = True
        time.sleep(min(WAIT_STEP, max(deadline - time.monotonic(), 0.0)))
    log(_logged("folder start", ("--status",), status))
    if status.code != 0:
        log(f"folder start: no launcher runs on these settings {LAUNCHER_WAIT:g} seconds after its start; the launcher's lines above say "
            "why; nothing to wait for")
        return False
    return True


def _opened(log: _Log, answers, open_page, wait_seconds: float, told: _Told) -> int:
    """Wait until the page answers, at most wait_seconds, close the information box and open the page in the default browser."""
    deadline = time.monotonic() + wait_seconds
    while not answers(PAGE):
        if time.monotonic() >= deadline:
            log(f"folder start: the page did not answer within {wait_seconds:g} seconds")
            told.failed(NOT_YET)
            return 1
        time.sleep(min(WAIT_STEP, max(deadline - time.monotonic(), 0.0)))
    told.close()                                 # gone when the page opens
    try:
        opened = open_page(PAGE)
    except Exception:                            # webbrowser gives False when no browser opens; a browser that fails says the same
        opened = False
    if not opened:
        log("folder start: the browser could not be opened")
        told.failed(NOT_OPENED)
        return 1
    log("folder start: the page answers and was opened in the browser")
    return 0


def stop(data: Path, run=subprocess.run) -> int:
    """Stop the aid of the folder with the launcher's --stop, which puts the STOP file in place, so that the aid stays off at the
    next logon too; 0 when the stop was made. --stop ends with 0 also when the parts have not ended within its wait: the window says
    that the assistant has stopped only when the launcher's state file is gone, and else to wait before the folder is moved or
    deleted. A folder that never started has nothing to stop: no data folder is made for it."""
    data = Path(data).resolve()
    _say(STOPPING)
    if not (data / SETTINGS_FILE).is_file():
        _say(STOPPED)
        return 0
    log = _Log(data)
    ran = _launcher(run, data, "--stop")
    log(_logged("folder stop", ("--stop",), ran))
    if ran.code != 0:
        _say(NOT_STOPPED)
        return 1
    if (data / STATE_FILE).exists():
        log("folder stop: the launcher's state file is still there: the launcher is still stopping its parts")
        _say(STILL_STOPPING)
        return 0
    _say(STOPPED)
    return 0


def no_autostart(data: Path, run=subprocess.run) -> int:
    """End the start at logon with the launcher's --remove-autostart; 0 when it is removed (or was not set)."""
    data = Path(data).resolve()
    ran = _launcher(run, data, "--remove-autostart")
    _Log(data)(_logged("folder no-autostart", ("--remove-autostart",), ran))
    if ran.code != 0:
        _say(AUTOSTART_KEPT)
        return 1
    _say(NO_AUTOSTART)
    return 0


# --- the command ---

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aid.portable", description="The aid from a folder: build it, start it, stop it.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", type=Path, metavar="OUT",
                        help="make the folder in OUT (on a Windows PC, with the Python that will run the aid)")
    action.add_argument("--start", type=Path, metavar="DATA", help="start the aid of the folder whose data folder is DATA")
    action.add_argument("--stop", type=Path, metavar="DATA", help="stop it; it stays off, also at the next logon, until --start")
    action.add_argument("--no-autostart", type=Path, metavar="DATA", help="no longer start it at logon")
    parser.add_argument("--python-zip", type=Path, help="with --build: the embeddable package of python.org, instead of a download")
    args = parser.parse_args(argv)
    if args.python_zip is not None and args.build is None:
        parser.error("--python-zip goes with --build")
    if args.build is not None:
        return build(args.build, python_zip=args.python_zip)
    for chosen, function, failed in ((args.start, start, NOT_STARTED), (args.stop, stop, NOT_STOPPED),
                                     (args.no_autostart, no_autostart, AUTOSTART_KEPT)):
        if chosen is not None:
            try:
                return function(chosen)
            except KeyboardInterrupt:            # the window is being closed, or Ctrl-C in it
                return 130
            except Exception as exc:             # a fault: its traceback goes to start.log, which the Dutch line names
                log, who = _Log(Path(chosen).resolve()), f"folder {function.__name__.replace('_', '-')}"
                log(f"{who}: the command failed ({type(exc).__name__})")
                for row in traceback.format_exc().splitlines():
                    log(row)
                told = _Told(BOX, log, who)      # the line in the window, or without one in a warning box
                told.failed(failed)
                told.warn()
                return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
