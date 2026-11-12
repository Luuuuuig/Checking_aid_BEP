"""One launcher for the three parts of the checking aid, and the start at logon on Windows.

    python -m aid.start --settings <file> [--testshop] [--poll-seconds N] [--background]    start the three parts and keep them running
    python -m aid.start --settings <file> --status                                          how the launcher and the parts are
    python -m aid.start --settings <file> --stop                                            create STOP and wait for the parts to end
    python -m aid.start --settings <file> --resume [--testshop] [...] [--background]        delete STOP, then start as above
    python -m aid.start --settings <file> [--testshop] [--poll-seconds N] --install-autostart   Windows: start at logon, and now
    python -m aid.start --settings <file> --remove-autostart                                Windows: no longer start at logon

The launcher starts the runner (python -m aid.runner), the review page (python -m aid.review) and the capture server
(python -m aid.capture_server) as child processes, with the Python that runs the launcher itself (its full path, because on Windows
the word "python" may be a placeholder that does nothing), with the folder that holds the package aid as their working folder, the
settings file by its absolute path, stdin closed, and no console window on Windows. Their environment is the launcher's own plus
PYTHONIOENCODING=utf-8 and PYTHONUNBUFFERED=1, and each is started with -X utf8 -u, so that each part's output file holds every
line at once and in one encoding also under a Python that does not read those two variables. A launcher that runs isolated (-I, as
every Python of the aid from a folder does, aid/portable.py) starts its parts with -I as well, and so does its start at logon, so that
the user's own site-packages and Python's environment variables never reach them. Each part's output is appended to
<settings folder>/logs/runner.out, review.out or capture.out; the launcher's own lines, with the time, go to logs/start.log and to
stdout when there is one (under pythonw there is none).

A launcher started in a window lives in that window, and says so first: closing the window ends it. --background makes the checks
of a start in the window, so that the reason is read there when nothing is started, and then starts the launcher apart from the
window (on Windows with pythonw.exe when it stands beside the Python, without a console, and outside the job of the window when
that is allowed; elsewhere in a session of its own) and returns.

The parts end with the launcher. On Windows the launcher makes one job object that Windows closes when the launcher ends in any
way (a closed window, Task Manager, a logoff), puts every part in it right after its start, and never closes it itself: the parts
are then ended by Windows. What a part starts itself (the browser that the review page opens for a sign-in) is not in the job and
stays. When the tie cannot be made, start.log says so once and the launcher goes on. Elsewhere the parts are started in a session
of their own, so that a Ctrl-C or a hangup of the terminal reaches the launcher only, and the launcher stops them.

Before anything is started: the settings file is read with load_settings (when it does not exist the launcher says so, when it
cannot be read it says why, by the fields and never a value, and ends with exit code 2; so it does, with the one sentence of
aid/settings.py, for a file in which exact_base_url is set, since no command builds a write route into Exact); a STOP file is obeyed; a launcher that
already runs on the settings folder is left alone; a runner that the last launcher may have left running is given time to end (see
the state file below); and the two addresses of the review page and the capture server are asked. They are fixed, so only one
launcher runs on a PC: when one answers, nothing is started, the line says so (a launcher on other settings is stopped with --stop
on its own settings, and parts started by hand are closed), a second line names the parts that a leftover state file records as
running and that were not tied to its launcher, with their process ids, and the exit code is 3. Then the launcher claims the
folder: it writes its state file, waits a second and reads it back, and starts nothing (exit code 3) when another launcher started
in the same moment wrote it last.

The launcher looks at the parts every second. A part that ends with exit code 2 (its settings were refused, for example the capture
server without a capture_token) is not started again until the settings file changes (its modification time is looked at every
second); that start is not one of the restarts. A part that ends otherwise is started again after 5 seconds, at most 5 times in any
10 minutes; after that it is given up and the other parts keep running. When every part has ended or been given up, the launcher
ends with exit code 1. A part that cannot be started is logged with the error itself.

The stop switch is the runner's own: the file STOP next to the settings. While it exists, the launcher starts nothing, writes one
line and ends with exit code 3. When it appears while the launcher runs, the review page and the capture server are ended
(terminate, and kill after 10 seconds), the runner is left to end by itself at its next pass and is ended too after its poll time
plus 30 seconds, the state file is removed and the launcher ends with exit code 0. Ctrl-C, SIGTERM, SIGHUP, SIGQUIT and SIGBREAK
(where the system has them) start the same stop: the launcher then makes the STOP file itself, with the text "launcher <time>", so
that the runner ends by itself, and deletes it when the parts have ended. A STOP file that was already there is the buyer's switch:
a stop never changes or deletes it. A second Ctrl-C or signal ends the parts at once. The signals are noted and acted on at the
launcher's next look, at most a second later, so that nothing is cut off halfway; a signal within a second of the one that asked is
the same request (a terminal that is closed sends more than one), and a signal that was ignored before the launcher (nohup) stays
ignored. The end of a stop cannot be skipped: the parts still alive are killed, the state file and the launcher's own STOP file are
removed and "stopped" is written while Ctrl-C and the signals are ignored; in the command they stay ignored until the process has
ended, so that a late one does not change the exit code. A start that finds a STOP file of a launcher ("launcher ...") while no
launcher runs removes it: it is left from a stop that was cut off. The launcher's own STOP file is removed by moving it aside and
reading it there, so that a STOP file which --stop puts in place in the same moment is put back and obeyed. --stop writes its STOP
file beside STOP and moves it over it (once more after a short sleep when the file is held open), says that it waits and waits up
to 90 seconds for the launcher's state file to disappear; when no launcher runs it names the parts that still answer, which do not
obey STOP. --resume deletes STOP and starts; while a launcher is still stopping (STOP exists and its heartbeat is fresh) it first
waits, at most 150 seconds, for that launcher to end, and leaves STOP alone when it has not; when the state file of another launcher
takes the place of the one it waits for (a second --resume started one meanwhile), it starts nothing.

<settings folder>/start_state.json holds the launcher's start, a heartbeat written every 5 seconds, the runner's poll time, and per
part the name of its command, its start, the number of starts, whether it was given up, whether it was tied to the launcher and,
while it runs, its process id. Every write goes through a temporary file with a name of the launcher's own. No process is ever
probed or ended through its process id: on Windows that would end it. Whether a launcher runs is told by the heartbeat only. A start
that finds a heartbeat older than 20 seconds takes the file for a leftover. A younger one is watched for 7 seconds, after a line that
says so: when it moves a launcher runs (exit code 3, "already running"), and when it has not moved the file is a leftover all the
same (a quick log off and on). When a leftover names a runner that was not tied to its launcher as running, that runner may still
run: the start makes the launcher's own STOP file (or keeps a leftover one), says so, waits the runner's poll time plus 5 seconds
(60 plus 5 when none is recorded), removes that STOP file and goes on. --background and --install-autostart make these checks in
the window and remove the leftover there, so that the launcher they start does not watch or wait again; a launcher replaces it.
--status and --stop answer at once, by the 20 seconds alone. After an end without a stop --status shows a part that was tied as
ended with the launcher, an untied runner as one that may still run until it sees STOP, and an untied review page or capture server
as a leftover while its address answers.

--install-autostart (Windows only) puts the shortcut Inkoopassistent.lnk in the user's own Startup folder (the one the registry
names, else the one under APPDATA), which runs pythonw.exe beside the Python in use (no window) with the launcher's arguments; it
needs no administrator rights. PowerShell makes the shortcut through the WScript.Shell COM object, with a fixed script that reads
the values from environment variables, so that no value is ever part of a command text; the script reads the saved shortcut back and
deletes it when a value differs. When no shortcut is made, Inkoopassistent.cmd in the same folder does the same (ASCII, or UTF-8
with its code page set when a path holds another character; no character is ever replaced); when that cannot be written either, the
command says that the start at logon could not be set, starts nothing and ends with exit code 1. Otherwise the launcher is started
as --background does, after the same checks. --remove-autostart deletes both files.

A fault of the launcher is written to start.log with its traceback, since under pythonw there is no window to show it, and the
command ends with exit code 1. A Ctrl-C outside the supervision (the watch of a start, the waits of --stop and --resume) ends the
command with one line that says what stands, without a traceback.

Exit codes: 0 a clean stop or a done action, also --install-autostart once the start at logon is set, whatever became of the start
now, and --stop whatever became of the stop; 1 every part ended, a fault, a start at logon that could not be set, --status when no
launcher runs, a start in the background that cannot be made, or --remove-autostart that cannot delete a file; 2 the settings or an
option refused (also a settings file that does not exist); 3 not started (STOP exists, a launcher already runs, a part already
answers, another launcher claimed the folder in the same moment, or --resume found that the launcher it waited for has not ended or
that another one was started meanwhile); 130 a Ctrl-C outside the supervision.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

from aid.settings import WriteRouteSet, load_settings

PACKAGE_PARENT = Path(__file__).resolve().parent.parent   # the folder that holds the package aid: the parts' working folder
STOP_FILE = "STOP"                               # the runner's stop switch (aid/runner.py), next to the settings file
OWN_STOP = "launcher "                           # the start of the text of a STOP file that a launcher made for its own stop
STATE_FILE = "start_state.json"                  # next to the settings file
LOGS = "logs"                                    # next to the settings file
PARTS = ("runner", "review", "capture")
TICK = 1.0                                       # seconds between two looks at the parts
RESTART_DELAY = 5.0                              # seconds before a part that ended is started again
RESTART_LIMIT = 5                                # restarts in RESTART_WINDOW after which a part is given up
RESTART_WINDOW = 600.0
REFUSED = 2                                      # the exit code of a part whose settings were refused
RUNNING, WAITING, GIVEN_UP, ENDED_REFUSED, STOPPED_PART = "running", "waiting to start again", "given up", "ended (exit code 2)", "stopped"
HEARTBEAT_EVERY = 5.0
HEARTBEAT_FRESH = 20.0                           # a heartbeat younger than this may be a launcher that runs
WATCH = HEARTBEAT_EVERY + 2.0                    # a heartbeat that has not moved in this time is not written any more
END_GRACE = 10.0                                 # seconds between ending a part gently and killing it
RUNNER_GRACE = 30.0                              # seconds beyond its poll time the runner gets to end by itself
DEFAULT_POLL = 60.0                              # the runner's own default poll time
STOP_WAIT = 90.0                                 # how long --stop waits for the parts to end
RESUME_WAIT = 150.0                              # how long --resume waits for a launcher that is still stopping
RETRY = 0.2                                      # seconds before a file that could not be removed is tried once more
SAME_REQUEST = 1.0                               # a signal within this time of the one that asked for the stop is the same request
LEFT_RUNNER_GRACE = 5.0                          # seconds beyond its poll time a runner that may be left behind gets to end by STOP
CHECK_TIMEOUT = 2.0                              # seconds each address gets to answer
ADDRESSES = {"review": "http://127.0.0.1:7860/", "capture": "http://127.0.0.1:7870/health"}   # fixed: one launcher runs on a PC
# UTF-8 and no buffering, on each part's command line, for a Python that does not read PYTHONIOENCODING and PYTHONUNBUFFERED, which
# the parts' environment holds for any other: one started with -I, as the parts of a launcher that runs isolated are (_isolated).
PART_OPTIONS = ("-X", "utf8", "-u")
ISOLATED = "-I"                                  # the user's own site-packages and Python's environment variables left out
SHORTCUT = "Inkoopassistent"
STOPPED = "stopped by the STOP file: delete it, or use --resume"
WINDOW = "the launcher runs in this window: closing the window stops the parts (use --background to run it without a window)"
WATCHING = f"a launcher may still run on these settings: watching its heartbeat for {WATCH:g} seconds"
ONE_LAUNCHER = ("only one launcher runs on a PC; a launcher on other settings is stopped with --stop on its own settings, "
                "and parts started by hand are closed")
STOP_SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGHUP", "SIGQUIT", "SIGBREAK") if hasattr(signal, name))
KILL_ON_JOB_CLOSE = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
SILENT_BREAKAWAY_OK = 0x1000                     # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK: what a part starts is not in the job
EXTENDED_LIMIT_INFORMATION = 9                   # JobObjectExtendedLimitInformation
CREATE_BREAKAWAY_FROM_JOB = 0x01000000           # a process creation flag of Windows: the new process is not in its parent's job
SHELL_FOLDERS = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders"   # under HKEY_CURRENT_USER
# The fixed script that makes the shortcut; its values come from the environment, never from the command text. It reads the saved
# shortcut back, because the shortcut object may lose a character its code page lacks: such a shortcut is deleted, with exit code 1.
POWERSHELL_SCRIPT = ("$ErrorActionPreference = 'Stop'; "
                     "$shell = New-Object -ComObject WScript.Shell; "
                     "$link = $shell.CreateShortcut($env:AID_LNK); "
                     "$link.TargetPath = $env:AID_TARGET; "
                     "$link.Arguments = $env:AID_ARGS; "
                     "$link.WorkingDirectory = $env:AID_CWD; "
                     "$link.WindowStyle = 7; "
                     "$link.Description = 'Inkoopassistent'; "
                     "$link.Save(); "
                     "$saved = $shell.CreateShortcut($env:AID_LNK); "
                     "if ($saved.TargetPath -ine $env:AID_TARGET -or $saved.Arguments -ine $env:AID_ARGS "
                     "-or $saved.WorkingDirectory -ine $env:AID_CWD) "
                     "{ Remove-Item -LiteralPath $env:AID_LNK -Force; exit 1 }")


def _windows() -> bool:
    return os.name == "nt"


def _number(value: float) -> str:
    """A poll time as the command line writes it: 5 for 5.0, 1.5 for 1.5."""
    return f"{value:g}"


def _isolated() -> bool:
    """Whether this Python runs isolated (-I): the launcher of the aid from a folder does (aid/portable.py), a launcher of a copy of
    the repository does not, and its parts find the package aid through their working folder, which -I would leave out."""
    return bool(sys.flags.isolated)


def _python_options(*options: str) -> list[str]:
    """The options before -m: -I first when the launcher runs isolated (_isolated), then these."""
    return [ISOLATED, *options] if _isolated() else list(options)


def commands(settings_file: Path, testshop: bool, poll_seconds: float | None, python: str) -> dict[str, list[str]]:
    """The three child commands, with the settings file by its absolute path, each started with -X utf8 -u (see PART_OPTIONS),
    and with -I when the launcher runs isolated."""
    settings, options = str(Path(settings_file).resolve()), _python_options(*PART_OPTIONS)
    runner = [python, *options, "-m", "aid.runner", "--settings", settings]
    if poll_seconds is not None:
        runner += ["--poll-seconds", _number(poll_seconds)]
    capture = [python, *options, "-m", "aid.capture_server", "--settings", settings] + (["--testshop"] if testshop else [])
    return {"runner": runner, "review": [python, *options, "-m", "aid.review", "--settings", settings], "capture": capture}


def _print(line: str) -> None:
    """A line to stdout when there is one (under pythonw sys.stdout is None)."""
    out = sys.stdout
    if out is None:
        return
    try:
        print(line, file=out, flush=True)
    except (OSError, ValueError, UnicodeError):
        pass


class _Log:
    """The launcher's lines: to <folder>/logs/start.log with the time, and to stdout. A log that cannot be written is passed over."""

    def __init__(self, folder: Path, wall=time.time):
        self.file, self.wall = folder / LOGS / "start.log", wall

    def __call__(self, line: str) -> None:
        stamp = datetime.fromtimestamp(self.wall()).isoformat(sep=" ", timespec="seconds")
        try:
            self.file.parent.mkdir(exist_ok=True)
            with open(self.file, "a", encoding="utf-8") as log:
                log.write(f"{stamp} {line}\n")
        except OSError:
            pass
        _print(line)

    def fault(self, exc: BaseException, line: str) -> None:
        """The line and the traceback of the fault that is being handled (under pythonw no window shows it); once for a fault."""
        if getattr(exc, "in_start_log", False):
            return
        self(line)
        for row in traceback.format_exc().splitlines():
            self(row)
        try:
            exc.in_start_log = True
        except AttributeError:
            pass


# --- the tie of the parts to the launcher (Windows) ---

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """As Windows defines it; the types have their Windows sizes on every system (a DWORD is 32 bits, a SIZE_T as wide as a pointer)."""
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32), ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Job:
    """The tie of the parts to the launcher. On Windows: one job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, made at the first
    part, in which every started part is put. The launcher keeps the job's handle for its whole life and never closes it, so when
    the launcher ends in any way Windows closes the handle and ends the parts. With JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK what a part
    starts itself (the browser the review page opens for a sign-in) is not in the job, and is not ended with the launcher. Elsewhere
    it does nothing."""

    def __init__(self):
        self.kernel32 = None
        self.handle = None
        self.refused = False                     # the job object could not be made: it is not made at the next part

    def __call__(self, proc) -> bool:
        """Put a started process in the job object: True when it is in it, False off Windows (not tied); an exception when that
        cannot be done."""
        if not _windows():
            return False
        if self.handle is None:
            if self.refused:
                raise OSError("the job object could not be made")
            self.refused = True
            self._make()
            self.refused = False
        if not self.kernel32.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise _failed("AssignProcessToJobObject")
        return True

    def _make(self) -> None:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE      # as wide as a pointer: a 64-bit handle is not cut
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _failed("CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK
        if not kernel32.SetInformationJobObject(job, EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
            raise _failed("SetInformationJobObject")
        self.kernel32, self.handle = kernel32, job


def _failed(call: str) -> OSError:
    """The error of a call of kernel32 that gave a zero result, with the error code Windows kept for it."""
    return OSError(f"{call} failed with error {getattr(ctypes, 'get_last_error', lambda: 0)()}")


# --- the state file, and the signals ---

def _read_state(folder: Path) -> dict | None:
    try:
        state = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _heartbeat(state: dict | None) -> float | None:
    launcher = (state or {}).get("launcher")
    value = launcher.get("heartbeat") if isinstance(launcher, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _who(state: dict | None) -> tuple:
    """Which launcher wrote a state file: its process id and its start."""
    launcher = (state or {}).get("launcher")
    return (launcher.get("pid"), launcher.get("started")) if isinstance(launcher, dict) else (None, None)


def _fresh(state: dict | None, wall) -> bool:
    """Whether the heartbeat of a state file is younger than HEARTBEAT_FRESH seconds: the only sign of a launcher that runs."""
    heartbeat = _heartbeat(state)
    return heartbeat is not None and abs(wall() - heartbeat) < HEARTBEAT_FRESH


def _running(folder: Path, wall=time.time) -> dict | None:
    """The state file of this folder when its heartbeat is fresh (_fresh), else None."""
    state = _read_state(folder)
    return state if _fresh(state, wall) else None


def _ended(folder: Path, limit: float, sleep, clock, who: tuple | None = None) -> bool | None:
    """Watch the state file of this folder for at most limit seconds. True as soon as the file is gone or its heartbeat has stood
    still for WATCH seconds: the launcher that wrote it has ended (a launcher that runs writes it every HEARTBEAT_EVERY seconds).
    False when the heartbeat has moved within the last WATCH seconds once limit is reached. None (only with who, a launcher's
    process id and start) as soon as the file names another launcher: that one has ended, and another one was started. The start
    of the watch and its deadline are one reading of the clock, and the watch is judged at the deadline, so with a limit of WATCH a
    heartbeat that never moved has always stood still."""
    since = clock()
    deadline = since + limit
    seen = _heartbeat(_read_state(folder))
    while (folder / STATE_FILE).exists():
        now = clock()
        if now >= since + WATCH:
            return True
        if now >= deadline:
            return False
        sleep(min(TICK, deadline - now, since + WATCH - now))
        state = _read_state(folder)
        if who is not None and state is not None and _who(state) != who:
            return None
        beat = _heartbeat(state)
        if beat is not None and beat != seen:    # a file that cannot be read for a moment is not a heartbeat that moved
            if seen is not None:
                since = clock()
            seen = beat
    return True


def _left_running(state: dict | None, tied: bool = False) -> dict[str, int]:
    """The parts that a state file records as running, with their process ids: those that were not tied to its launcher (they
    can outlive it), or with tied those that were (on Windows they ended with it)."""
    parts = (state or {}).get("parts")
    found = {}
    for name in PARTS:
        part = parts.get(name) if isinstance(parts, dict) else None
        if (isinstance(part, dict) and part.get("state") == RUNNING and isinstance(part.get("pid"), int)
                and (part.get("tied") is True) == tied):
            found[name] = part["pid"]
    return found


def _runner_poll(state: dict | None) -> float:
    """The poll time of the runner that a state file names; the runner's own default when it records none."""
    launcher = (state or {}).get("launcher")
    value = launcher.get("runner_poll_seconds") if isinstance(launcher, dict) else None
    recorded = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    return float(value) if recorded else DEFAULT_POLL


def _now_text(wall) -> str:
    return datetime.fromtimestamp(wall()).isoformat(timespec="seconds")


# --- the STOP file ---

def _make_stop(folder: Path, text: str) -> bool:
    """Make the STOP file with this text for a stop of a launcher's own, only when there is none (exclusive creation): a STOP file
    that is there is the buyer's switch, or another launcher's. True when it was made; an OSError when it cannot be made."""
    try:
        with open(folder / STOP_FILE, "x", encoding="utf-8") as stop_file:
            stop_file.write(text)
    except FileExistsError:
        return False
    return True


def _remove_own_stop(folder: Path, own, log) -> bool | None:
    """Remove the STOP file of a launcher's own stop (own: whether a text is that). The file is moved aside first and read there,
    so that a STOP file which --stop puts in place in the same moment is never deleted: one that is not a launcher's own is put
    back. True when it was removed; False when it was put back (the buyer's: it is obeyed); None when there was none to move (gone
    already, or held by another program: a later start removes a launcher's own STOP file that stayed behind)."""
    path, aside = folder / STOP_FILE, folder / f".{STOP_FILE}.{os.getpid()}.aside"
    try:
        os.replace(path, aside)
    except OSError:
        return None
    try:
        mine = own(aside.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        mine = False
    if mine:
        try:
            aside.unlink()
        except OSError:
            pass
        return True
    try:
        os.replace(aside, path)
    except OSError as exc:
        log(f"the STOP file could not be put back ({type(exc).__name__}): it is {aside.name} now; rename it to {STOP_FILE}")
    return False


def _place_stop(folder: Path, sleep) -> None:
    """Put an empty STOP file in place, as --stop does: written beside STOP and moved over it, so that a STOP file which a launcher
    is making or removing in the same moment is replaced, never emptied in place (where the launcher's text could land afterwards,
    and the file would count as the launcher's own). The move is tried once more after RETRY seconds when the file is held open."""
    beside = folder / f".{STOP_FILE}.{os.getpid()}.new"
    beside.write_text("", encoding="utf-8")
    try:
        os.replace(beside, folder / STOP_FILE)
    except OSError:
        sleep(RETRY)
        try:
            os.replace(beside, folder / STOP_FILE)
        except OSError:
            beside.unlink(missing_ok=True)
            raise


def _modified(path: Path) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class _Asked(KeyboardInterrupt):
    """A signal asked the launcher to stop, as Ctrl-C does; its arguments are the signal's number and the clock's time it came at."""


def _asker(asked: BaseException) -> str:
    """What asked for the stop, as the log names it: Ctrl-C, or the signal by its name."""
    number = asked.args[0] if isinstance(asked, _Asked) and asked.args else signal.SIGINT
    return "Ctrl-C" if number == signal.SIGINT else signal.Signals(number).name


def _set_handlers(handlers: dict) -> dict:
    """Set these signal handlers and return the ones they replace, for this same function to put back. Only in the main thread:
    Python delivers a signal nowhere else, and lets no other thread set a handler."""
    earlier = {}
    if threading.current_thread() is not threading.main_thread():
        return earlier
    for number, handler in handlers.items():
        try:
            earlier[number] = signal.signal(number, handler if handler is not None else signal.SIG_DFL)
        except (OSError, ValueError):            # a signal that this system does not let a program handle
            pass
    return earlier


class _Part:
    def __init__(self, name: str, cmd: list[str]):
        self.name, self.cmd = name, cmd
        self.command = cmd[cmd.index("-m") + 1] if "-m" in cmd[:-1] else Path(cmd[0]).name
        self.proc = None
        self.pid: int | None = None
        self.state = "not started"
        self.started = None                      # the wall time of its last start, as text
        self.starts = 0
        self.restarts: list[float] = []          # the clock times of its restarts
        self.due: float | None = None            # when it is to be started again
        self.settings_seen: int | None = None    # the modification time of the settings file at its last start
        self.tied = False                        # whether the tie took its process (Windows then ends it with the launcher)

    def record(self) -> dict:
        """What the state file holds of the part; its process id only while it runs, so that nobody ends another process by it."""
        return {"command": self.command, "pid": self.pid if self.state == RUNNING else None, "started": self.started,
                "starts": self.starts, "given_up": self.state == GIVEN_UP, "state": self.state, "tied": self.tied}


class _Supervisor:
    def __init__(self, settings_file: Path, parts: dict[str, list[str]], spawn, clock, sleep, wall, poll_seconds: float | None,
                 log: _Log, tie=None, ignore_after_stop: bool = False):
        self.settings_file, self.folder = settings_file, settings_file.parent
        self.spawn, self.clock, self.sleep, self.wall, self.log = spawn, clock, sleep, wall, log
        self.tie = tie if tie is not None else _Job()
        self.parts = [_Part(name, cmd) for name, cmd in parts.items()]
        self.runner_poll = poll_seconds if poll_seconds is not None else DEFAULT_POLL     # recorded for a start after a leftover
        self.runner_grace = self.runner_poll + RUNNER_GRACE
        self.ignore_after_stop = ignore_after_stop   # the command: the signals stay ignored from the end of a stop on
        self.started = self._now_text()
        self.beaten: float | None = None
        self.state_failed = False
        self.untied = False                      # the line of a tie that failed is written once
        self.noted: list[tuple[int, float]] = [] # the signals that asked and are not acted on yet, each with the clock's time
        self.stopping: float | None = None       # the clock's time of what asked for the stop, once a stop has begun
        self.own_stop: str | None = None         # the text of the STOP file the launcher made for its own stop
        self.finished = False

    def _now_text(self) -> str:
        return _now_text(self.wall)

    def _out(self, part: _Part) -> Path:
        return self.folder / LOGS / f"{part.name}.out"

    # --- the state file ---

    def _write(self) -> OSError | None:
        """Write the state file through a temporary file with a name of this launcher's own, so that two launchers that write in
        the same moment never take each other's; the error when it cannot be written."""
        state = {"launcher": {"pid": os.getpid(), "started": self.started, "heartbeat": self.wall(), "heartbeat_at": self._now_text(),
                              "runner_poll_seconds": self.runner_poll},
                 "parts": {part.name: part.record() for part in self.parts}}
        temporary = self.folder / f".{STATE_FILE}.{os.getpid()}.tmp"
        try:
            temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(temporary, self.folder / STATE_FILE)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return exc
        self.beaten = self.clock()
        return None

    def write_state(self) -> None:
        failed = self._write()
        if failed is not None:                   # for example the file held open by --status on Windows: tried again at the next beat
            if not self.state_failed:
                self.log(f"the state file ({STATE_FILE}) could not be written ({type(failed).__name__}); trying again")
            self.state_failed = True
            return
        self.state_failed = False

    def claim(self) -> bool:
        """Claim the folder before the first part is started: write the state file, wait one tick and read it back. True when it
        still names this launcher (its process id and start). Otherwise another launcher was started on these settings in the same
        moment and its claim stands: this one starts nothing and leaves the file alone."""
        failed = self._write()
        if failed is not None:
            self.log(f"the state file ({STATE_FILE}) could not be written ({type(failed).__name__}): nothing started")
            return False
        self.sleep(TICK)
        if _who(_read_state(self.folder)) != (os.getpid(), self.started):
            self.log("a launcher was started on these settings meanwhile; nothing started")
            return False
        return True

    def beat(self) -> None:
        if self.beaten is None or self.clock() - self.beaten >= HEARTBEAT_EVERY:
            self.write_state()

    def remove_state(self) -> None:
        """Remove the state file. When that fails (on Windows while another program reads the file), it is tried once more after a
        short sleep."""
        path = self.folder / STATE_FILE
        for last in (False, True):
            try:
                path.unlink()
            except FileNotFoundError:
                return
            except OSError as exc:
                if last:
                    self.log(f"the state file ({STATE_FILE}) could not be removed ({type(exc).__name__})")
                    return
                self.sleep(RETRY)
            else:
                return

    # --- the STOP file of the launcher's own stop ---

    def make_stop(self) -> None:
        """Make the STOP file for the time of a stop that the launcher starts itself, so that the runner ends by itself at its
        next pass. A STOP file that is already there is the buyer's switch: it is left as it is, and is not the launcher's."""
        text = f"{OWN_STOP}{self._now_text()}"
        try:
            if _make_stop(self.folder, text):
                self.own_stop = text
        except OSError as exc:
            self.log(f"the STOP file could not be made ({type(exc).__name__}): the runner is ended after its wait")

    def remove_stop(self) -> None:
        """Delete the STOP file of the launcher's own stop, the way _remove_own_stop does: a STOP file that --stop put in place
        during the stop holds another text, is the buyer's, and stays."""
        if self.own_stop is not None:
            text, self.own_stop = self.own_stop, None
            _remove_own_stop(self.folder, lambda found: found == text, self.log)

    # --- starting and ending ---

    def start(self, part: _Part, restart: bool = True) -> None:
        """Start a part. A start after an end is one of its RESTART_LIMIT restarts in RESTART_WINDOW; a start after a change of
        the settings file (restart False) is not."""
        again = part.starts > 0
        part.starts += 1
        part.due = None
        if again and restart:
            part.restarts.append(self.clock())
        part.settings_seen = _modified(self.settings_file)
        try:
            (self.folder / LOGS).mkdir(exist_ok=True)
            with open(self._out(part), "ab") as out:    # the child keeps its own handle; the launcher closes its own at once
                part.proc = self.spawn(part.cmd, cwd=str(PACKAGE_PARENT), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=not _windows())
        except OSError as exc:
            part.proc = None
            self.ended(part, f"{part.name} could not be started ({type(exc).__name__}: {exc})", None)
            return
        try:
            part.tied = bool(self.tie(part.proc))
        except Exception as exc:                 # a zero result, an OSError, a missing attribute: the launcher goes on without the tie
            part.tied = False
            if not self.untied:
                self.log(f"the parts could not be tied to the launcher ({type(exc).__name__}: {exc}): "
                         "if the launcher is ended from outside, close the parts by hand")
            self.untied = True
        part.state, part.started, part.pid = RUNNING, self._now_text(), part.proc.pid
        self.log(f"started {part.name}{' again' if again else ''} (process {part.proc.pid}, start {part.starts})")

    def ended(self, part: _Part, line: str, code: int | None) -> None:
        """What follows the end of a part: not started again after exit code 2 (until the settings file changes), given up after
        RESTART_LIMIT restarts in RESTART_WINDOW, else started again after RESTART_DELAY. code None: the part could not be
        started, so it has no output to read."""
        read = f"; read {LOGS}/{part.name}.out" if code is not None else ""
        now = self.clock()
        if code == REFUSED:
            part.state = ENDED_REFUSED
            self.log(f"{line}: not started again until the settings file changes{read}")
        elif len([when for when in part.restarts if now - when < RESTART_WINDOW]) >= RESTART_LIMIT:
            part.state = GIVEN_UP
            self.log(f"{line}; {part.name} given up after {RESTART_LIMIT} restarts in 10 minutes, the other parts keep running{read}")
        else:
            part.state, part.due = WAITING, now + RESTART_DELAY
            self.log(f"{line}; started again in {_number(RESTART_DELAY)} seconds")

    def check(self) -> None:
        changed = False
        modified = _modified(self.settings_file)
        for part in self.parts:
            if part.proc is not None and part.proc.poll() is not None:
                code, part.proc = part.proc.returncode, None
                self.ended(part, f"{part.name} ended with exit code {code}", code)
                changed = True
            elif part.due is not None and self.clock() >= part.due:
                self.start(part)
                changed = True
            elif part.state == ENDED_REFUSED and modified is not None and modified != part.settings_seen:
                self.log(f"the settings file has changed: starting {part.name} again")
                self.start(part, restart=False)
                changed = True
        if changed:
            self.write_state()

    def _alive(self, parts: list[_Part]) -> list[_Part]:
        """The parts of these that still run, during a stop; one that has ended is logged, and the state file says so at once."""
        alive, ended = [], False
        for part in parts:
            if part.proc is None:
                continue
            if part.proc.poll() is None:
                alive.append(part)
            else:
                self.log(f"{part.name} ended with exit code {part.proc.returncode}")
                part.state, part.proc, ended = STOPPED_PART, None, True
        if ended:
            self.write_state()
        return alive

    def _note(self, number, frame) -> None:
        """The handler of Ctrl-C and the stop signals while the launcher runs: the signal is noted, and pause() acts on it."""
        self.noted.append((number, self.clock()))

    def _request(self) -> tuple[int, float] | None:
        """The oldest signal noted that asks for something new, with its time; None when there is none. A signal that came before
        the stop was asked for, or within SAME_REQUEST seconds of that, belongs to the request that started the stop (a terminal
        that is closed and a system that shuts down send more than one signal at a time): it is dropped, not taken for a second
        Ctrl-C."""
        while self.noted:
            number, when = self.noted.pop(0)
            if self.stopping is None or when - self.stopping >= SAME_REQUEST:
                return number, when
        return None

    def pause(self, seconds: float) -> None:
        """Sleep, unless a signal has asked already. A signal noted by the end of the sleep asks here, as an exception, at a place
        that expects it: so no start of a part, no line and no file is cut off halfway."""
        request = self._request()
        if request is None:
            self.sleep(seconds)
            request = self._request()
        if request is not None:
            raise _Asked(*request)

    def _wait(self, parts: list[_Part], seconds: float, sleep=None) -> list[_Part]:
        """Wait up to seconds for the parts to end; the parts still running."""
        sleep = sleep or self.pause
        deadline = self.clock() + seconds
        alive = self._alive(parts)
        while alive and self.clock() < deadline:
            self.beat()
            sleep(min(TICK, max(deadline - self.clock(), 0.01)))
            alive = self._alive(alive)
        return alive

    def _end(self, parts: list[_Part]) -> None:
        """Terminate the parts, and kill those that have not ended after END_GRACE seconds."""
        for part in self._alive(parts):
            try:
                part.proc.terminate()
            except OSError:
                pass
        for part in self._wait(parts, END_GRACE):
            self.log(f"{part.name} did not end within {_number(END_GRACE)} seconds: killed")
            try:
                part.proc.kill()
            except OSError:
                pass
        self._wait(parts, END_GRACE)

    def stop(self, asked: BaseException | None = None) -> int:
        """Stop the parts as the STOP file asks: the review page and the capture server are ended, the runner is left to end by
        itself at its next pass and is ended after its poll time plus RUNNER_GRACE. asked is the Ctrl-C or the signal when the
        launcher starts the stop itself (None: the STOP file asked): it then makes the STOP file for the time of the stop. A
        Ctrl-C or a signal during the stop ends the wait. Whatever happens, finish() ends the stop."""
        try:
            self.stopping = asked.args[1] if isinstance(asked, _Asked) else self.clock()
            if asked is not None:
                self.log(f"{_asker(asked)}: stopping the parts")
                self.make_stop()
            for part in self.parts:
                if part.due is not None:
                    part.due, part.state = None, STOPPED_PART
            runner = [part for part in self.parts if part.name == "runner"]
            others = [part for part in self.parts if part.name != "runner"]
            self._end(others)
            if self._alive(runner):
                self.log(f"waiting for the runner to finish its pass (up to {_number(self.runner_grace)} seconds)")
            if self._wait(runner, self.runner_grace):
                self.log("the runner did not end by itself: ending it")
                self._end(runner)
        except KeyboardInterrupt as again:
            self.log(f"{_asker(again)}{' again' if asked is not None else ''}: ending the parts at once")
        finally:
            self.finish()
        return 0

    def finish(self) -> None:
        """The end of a stop, which nothing skips: the parts still alive are killed, the state file and the launcher's own STOP
        file are removed and "stopped" is written. Ctrl-C and the stop signals are ignored meanwhile (in the main thread). The
        handlers of before are put back afterwards, except in the command (ignore_after_stop), where they stay ignored until the
        process has ended: a signal after "stopped" would end it by that signal instead of with exit code 0."""
        if self.finished:
            return
        self.finished = True
        earlier = _set_handlers({number: signal.SIG_IGN for number in (signal.SIGINT, *STOP_SIGNALS)})
        try:
            alive = self._alive(self.parts)
            for part in alive:
                try:
                    part.proc.kill()
                except OSError:
                    pass
            try:
                alive = self._wait(alive, END_GRACE, self.sleep)
            except KeyboardInterrupt:            # not from the keyboard, which is ignored here: only a sleep given by a caller raises it
                alive = []
            for part in alive:
                self.log(f"{part.name} (process {part.pid}) has not ended: close it by hand")
        finally:
            self.remove_state()
            self.remove_stop()
            self.log("stopped")
            if not self.ignore_after_stop:
                _set_handlers(earlier)

    def _supervise(self) -> bool:
        """Start the parts and keep them running. True when the STOP file appeared; False when every part has ended or been given
        up. A Ctrl-C or a signal leaves it as a KeyboardInterrupt."""
        stop_file = self.folder / STOP_FILE
        self.write_state()
        for part in self.parts:                  # the state after each start: the heartbeat stays fresh while slow starts follow
            self.start(part)                     # one another, and a launcher that is ended meanwhile leaves the process ids so far
            self.write_state()
        while True:
            if stop_file.exists():
                self.log("the STOP file appeared: stopping the parts")
                return True
            self.check()
            if all(part.state in (GIVEN_UP, ENDED_REFUSED) for part in self.parts):
                self.log("every part has ended or has been given up: the launcher ends")
                self.remove_state()
                return False
            self.beat()
            self.pause(TICK)

    def run(self) -> int:
        """Supervise the parts until the STOP file, a Ctrl-C or a stop signal asks for the stop (0), or every part has ended (1).
        In the main thread Ctrl-C and the stop signals (SIGTERM, SIGHUP, SIGQUIT, SIGBREAK) are noted while this runs, and the
        handlers of before are put back when it returns (in the command not after a stop: finish()). A signal that was ignored
        before stays ignored, and a Ctrl-C handler that is not Python's own stays."""
        wanted = {}
        for number in (signal.SIGINT, *STOP_SIGNALS):
            current = signal.getsignal(number)
            if current is not signal.SIG_IGN and (number != signal.SIGINT or current is signal.default_int_handler):
                wanted[number] = self._note
        earlier = _set_handlers(wanted)
        try:
            try:
                if not self._supervise():
                    return 1
                asked = None
            except KeyboardInterrupt as interrupt:
                asked = interrupt
            return self.stop(asked)
        except BaseException as exc:             # a fault of the launcher itself, whatever it is: no part is left running without it
            self.log.fault(exc, f"the launcher failed ({type(exc).__name__}): stopping the parts")
            try:
                self._end(self.parts)
            except KeyboardInterrupt:
                pass
            finally:
                self.finish()
            raise
        finally:
            if not (self.ignore_after_stop and self.finished):
                _set_handlers(earlier)


# --- the checks of a start ---

def _open_url(url: str, timeout: float):
    """Open a local address without any proxy of the environment."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout)


def _answers(url: str, opener=None) -> bool:
    try:
        with (opener or _open_url)(url, timeout=CHECK_TIMEOUT) as answer:
            return 200 <= answer.status < 400
    except Exception:                            # refused, timed out, an HTTP error: it does not answer
        return False


def _own_stop(stop_file: Path) -> bool | None:
    """None when there is no STOP file; True when a launcher made it for its own stop; False for every other one."""
    try:
        return stop_file.read_text(encoding="utf-8", errors="replace").startswith(OWN_STOP)
    except FileNotFoundError:
        return None
    except OSError:                              # it exists and cannot be read: obeyed, as the runner obeys it
        return False


def _refusal(folder: Path, log: _Log, sleep, clock, wall, opener, detached: bool = False) -> str | None:
    """The checks of a start, each with its line: "stop" when a STOP file exists that is not a launcher's own, "running" when a
    launcher runs on this folder, "answers" when the review page or the capture server already answers; None when the parts may be
    started.

    A heartbeat younger than HEARTBEAT_FRESH seconds is watched for WATCH seconds first, after a line that says so: only one that
    moves is a launcher that runs. A state file of a launcher that no longer runs is a leftover. When it names a runner that was not
    tied to that launcher as running, that runner may still run, and only STOP ends it: the launcher's own STOP file is made (or a
    leftover one kept) for its poll time plus LEFT_RUNNER_GRACE seconds and then removed. Otherwise a launcher's own STOP file is
    the leftover of a stop that was cut off, and is removed. Either removal moves the file aside and reads it there, so that a STOP
    file which --stop puts in place in the same moment is obeyed. The state file is read once more after the addresses were asked,
    for a launcher that started in that time.

    A leftover state file stays while something answers: its process ids are the way to close the parts it names. detached: the
    checks are made in the window for a launcher started apart from it (--background, --install-autostart), and the window removes
    the leftover, so that the launcher does not watch it again; otherwise the launcher replaces it, and says so after a watch."""
    stop_file = folder / STOP_FILE
    if _own_stop(stop_file) is False:
        log(STOPPED)
        return "stop"
    state, watched = _read_state(folder), False
    if _fresh(state, wall):
        log(WATCHING)
        if not _ended(folder, WATCH, sleep, clock):
            log(f"already running since {state['launcher'].get('started')}: a launcher runs on these settings; nothing started")
            return "running"
        state, watched = _read_state(folder), True   # the leftover, or None when that launcher ended and removed its file
    own = _own_stop(stop_file)                   # read now, after the watch: --stop may have written the file meanwhile
    if own is False:
        log(STOPPED)
        return "stop"
    runner = _left_running(state).get("runner")
    if runner is not None:                       # a runner the last launcher may have left running: it ends at its next pass
        wait = _runner_poll(state) + LEFT_RUNNER_GRACE
        if own is None:
            try:
                _make_stop(folder, f"{OWN_STOP}{_now_text(wall)}")
            except OSError as exc:
                log(f"the STOP file could not be made ({type(exc).__name__})")
        log(f"a runner that may be left behind (process {runner}) is given {_number(wait)} seconds to end by the STOP file")
        sleep(wait)
    if own or runner is not None:
        removed = _remove_own_stop(folder, lambda text: text.startswith(OWN_STOP), log)
        if removed is False:
            log(STOPPED)
            return "stop"
        if removed and own:
            log("a STOP file left by an interrupted stop was removed")
    seen = _who(state)                           # the leftover's launcher, or none
    answering = [url for url in ADDRESSES.values() if _answers(url, opener)]
    now = _read_state(folder)                    # asking the addresses takes seconds on Windows: a launcher may have started meanwhile
    if _fresh(now, wall) and _who(now) != seen:
        log(f"already running since {now['launcher'].get('started')}: a launcher was started on these settings meanwhile; "
            "nothing started")
        return "running"
    if answering:
        log(f"something already answers at {' and '.join(answering)}: {ONE_LAUNCHER}")
        left = _left_running(state)
        if left:
            log(f"the state file ({STATE_FILE}) of the last launcher names as running: "
                + ", ".join(f"{name} (process {pid})" for name, pid in left.items()))
        return "answers"
    if state is not None and detached:
        try:
            (folder / STATE_FILE).unlink(missing_ok=True)
        except OSError:                          # held open for a moment (Windows): the launcher replaces it
            log(f"found the state file ({STATE_FILE}) of a launcher that no longer runs")
        else:
            log(f"found the state file ({STATE_FILE}) of a launcher that no longer runs: it is removed")
    elif state is not None and watched:
        log(f"the state file ({STATE_FILE}) is left over from a launcher that no longer runs (its heartbeat does not move): "
            "it is replaced")
    return None


def supervise(settings_file: Path, parts: dict[str, list[str]], spawn=subprocess.Popen, clock=time.monotonic, sleep=time.sleep, *,
              wall=time.time, poll_seconds: float | None = None, opener=None, tie=None, window: bool = False,
              ignore_after_stop: bool = False) -> int:
    """Start the parts and keep them running (the module docstring); 0 after a stop, 1 when every part has ended, 3 when the
    checks of a start refuse it (_refusal) or another launcher claimed the folder in the same moment (_Supervisor.claim). opener
    asks the two addresses (the one of --status when None); tie is given every started process (the job object of Windows when
    None); window: say first that the launcher lives in this window; ignore_after_stop: the command, which ends after this, keeps
    Ctrl-C and the stop signals ignored from the end of a stop on."""
    settings_file = Path(settings_file).resolve()
    log = _Log(settings_file.parent, wall)
    if _refusal(settings_file.parent, log, sleep, clock, wall, opener) is not None:
        return 3
    supervisor = _Supervisor(settings_file, parts, spawn, clock, sleep, wall, poll_seconds, log, tie, ignore_after_stop)
    if not supervisor.claim():
        return 3
    if window:
        _print(WINDOW)
    return supervisor.run()


# --- --status, --stop and --resume ---

def status(settings_file: Path, opener=None, wall=time.time) -> int:
    """One line for the launcher and one per part; 0 when the launcher runs (a heartbeat younger than HEARTBEAT_FRESH seconds), 1
    otherwise. A part's process id is shown only while it runs. When the launcher does not run and its state file still records
    parts as running, it was ended from outside. A part that was tied to it ended with it (Windows). Of the others the runner may
    still run, until it sees STOP, and a review page or a capture server is a leftover while its address answers, and ended
    otherwise; a leftover is shown with the process id the file recorded."""
    folder = Path(settings_file).resolve().parent
    state = _read_state(folder)
    runs = _fresh(state, wall)
    launcher = state.get("launcher") if state is not None and isinstance(state.get("launcher"), dict) else {}
    recorded = state.get("parts") if state is not None and isinstance(state.get("parts"), dict) else {}
    left = {} if runs else _left_running(state)
    gone = {} if runs else _left_running(state, tied=True)
    stop_exists = (folder / STOP_FILE).exists()
    if runs:
        _print(f"launcher: running since {launcher.get('started')} (process {launcher.get('pid')})"
               + ("; it is stopping (the STOP file exists)" if stop_exists else ""))
    else:
        last = launcher.get("heartbeat_at")
        _print("launcher: not running"
               + (f"; it ended without stopping its parts (last heartbeat {last})" if left
                  else f"; it was ended from outside (last heartbeat {last}), and its parts ended with it" if gone else "")
               + (" (the STOP file exists)" if stop_exists else ""))
    for name in PARTS:
        part = recorded.get(name)
        url = ADDRESSES.get(name)
        answers = url is not None and _answers(url, opener)
        if runs and isinstance(part, dict):
            line = (f"{name}: {part.get('state')}" + (f", process {part.get('pid')}" if part.get("state") == RUNNING else "")
                    + f", started {part.get('started')}, {part.get('starts')} start(s)"
                    + (", given up" if part.get("given_up") and part.get("state") != GIVEN_UP else ""))
        elif name in gone:
            line = f"{name}: ended with the launcher"
        elif name in left and url is None:
            line = f"{name}: may still run (process {left[name]}); it obeys the STOP file"
        elif name in left:
            line = f"{name}: leftover, process {left[name]}, started {part.get('started')}" if answers else f"{name}: ended"
        elif left or gone:
            line = f"{name}: not running"
        else:
            line = f"{name}: not started by a launcher"
        if url is not None:
            line += f"; {url} " + ("answers" if answers else "does not answer")
        _print(line)
    return 0 if runs else 1


def stop(settings_file: Path, sleep=time.sleep, wall=time.time, clock=time.monotonic, opener=None) -> int:
    """Put STOP in place (_place_stop) and wait up to STOP_WAIT seconds for the launcher's state file to disappear; say whether the
    parts ended. When no launcher runs, STOP ends a runner only: the review page and the capture server are asked once, and for one
    that answers the line says that only one launcher runs on a PC, with the process id a leftover state file recorded for it
    when it was not tied to its launcher."""
    folder = Path(settings_file).resolve().parent
    _place_stop(folder, sleep)
    _Log(folder, wall)("--stop: the STOP file is created")
    running = _running(folder, wall)
    if running is None:
        _print("no launcher runs on these settings; the STOP file is there, so a runner started on its own also ends at its next pass")
        left = _left_running(_read_state(folder))
        for name, url in ADDRESSES.items():
            if _answers(url, opener):
                _print(f"{url} ({name}) still answers, and STOP does not end it: {ONE_LAUNCHER}"
                       + (f"; the state file ({STATE_FILE}) of the last launcher names it as running (process {left[name]})"
                          if name in left else ""))
        return 0
    _print(f"waiting for the parts to end (up to {_number(STOP_WAIT)} seconds)")
    deadline = clock() + STOP_WAIT
    while clock() < deadline:
        if not (folder / STATE_FILE).exists():
            _print("the parts have ended; start them again with --resume, or with --resume --background to run without a window")
            return 0
        now = _read_state(folder)
        if now is not None and _who(now) != _who(running):
            _print("the parts have ended, and another launcher was started on these settings meanwhile; check with --status")
            return 0
        if now is not None and not _fresh(now, wall):    # a file that cannot be read for a moment says nothing
            _print("the launcher stopped writing its heartbeat without ending cleanly; see logs/start.log and check with --status")
            return 0
        sleep(TICK)
    _print(f"the parts have not ended within {_number(STOP_WAIT)} seconds; the launcher goes on stopping them, check with --status")
    return 0


def resume(settings_file: Path, sleep=time.sleep, wall=time.time, clock=time.monotonic) -> int | None:
    """What --resume does before the start: delete STOP. While a launcher is still stopping (STOP exists and its heartbeat is
    younger than HEARTBEAT_FRESH seconds) it first waits, at most RESUME_WAIT seconds and with STOP left alone, for that launcher
    to end: its state file goes, or its heartbeat stops moving. The state file of another launcher (by its process id and start)
    also ends the wait: that one was started meanwhile (a second --resume), and nothing is started. None when the start may go on;
    3 when that launcher has not ended, or another one was started meanwhile."""
    folder = Path(settings_file).resolve().parent
    stop_file, log = folder / STOP_FILE, _Log(folder, wall)
    stopping = _running(folder, wall) if stop_file.exists() else None
    if stopping is not None:
        _print(f"a launcher is still stopping on these settings: waiting for it to end (up to {_number(RESUME_WAIT)} seconds)")
        ended = _ended(folder, RESUME_WAIT, sleep, clock, _who(stopping))
        if ended is None:
            log("--resume: another launcher was started on these settings meanwhile; nothing started; check with --status")
            return 3
        if not ended:
            log(f"--resume: that launcher has not ended within {_number(RESUME_WAIT)} seconds; nothing started and the STOP file "
                "left as it is; check with --status")
            return 3
    try:
        stop_file.unlink()
        log("--resume: the STOP file is deleted")
    except FileNotFoundError:
        pass
    return None


# --- the launcher without a window, and the start at logon (Windows) ---

def startup_folder() -> Path:
    """The user's Startup folder: where the Start Menu itself has it (the value Startup of the user's shell folders in the
    registry, which follows a Start Menu that the organisation moved), else under APPDATA."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as key:
            value, _ = winreg.QueryValueEx(key, "Startup")
        folder = winreg.ExpandEnvironmentStrings(value).strip() if isinstance(value, str) else ""
        if folder and "%" not in folder:
            return Path(folder)
    except Exception:                            # no registry (not Windows), no such key or value: the folder of every Windows
        pass
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _launcher(settings_file: Path, testshop: bool, python: Path, poll_seconds: float | None = None) -> dict:
    """How the launcher is started without a window: target (pythonw.exe beside python when it exists, else python), arguments
    (one text), argv (the same as a list; with -I first when this launcher runs isolated) and working_folder (the folder that holds
    the package)."""
    python = Path(python)
    pythonw = python.with_name("pythonw.exe")
    settings = str(Path(settings_file).resolve())
    options = (["--testshop"] if testshop else []) + (["--poll-seconds", _number(poll_seconds)] if poll_seconds is not None else [])
    first = _python_options()
    return {"target": str(pythonw) if pythonw.is_file() else str(python),
            "arguments": " ".join([*first, "-m", "aid.start", "--settings", f'"{settings}"', *options]),
            "argv": [*first, "-m", "aid.start", "--settings", settings, *options], "working_folder": str(PACKAGE_PARENT)}


def autostart_shortcut(settings_file: Path, testshop: bool, python: Path, poll_seconds: float | None = None, *,
                       startup: Path | None = None) -> dict:
    """The shortcut's values: path (in the user's Startup folder, or in startup when given), target, arguments, argv and
    working_folder (see _launcher)."""
    folder = Path(startup) if startup is not None else startup_folder()
    return {"path": folder / f"{SHORTCUT}.lnk", **_launcher(settings_file, testshop, python, poll_seconds)}


def start_detached(values: dict, spawn=subprocess.Popen):
    """Start the launcher (the target and argv of _launcher) apart from this command and without a window. On Windows: detached
    from the console and in a process group of its own, first also outside the job of this command's window, so that closing the
    window does not end it, and once more without that when Windows refuses it (a job that lets no process leave). Elsewhere in
    a session of its own. An OSError when it cannot be started."""
    cmd = [values["target"], *values["argv"]]
    options = {"cwd": values["working_folder"], "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "close_fds": True}
    if not _windows():
        return spawn(cmd, start_new_session=True, **options)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        return spawn(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **options)
    except OSError:
        return spawn(cmd, creationflags=flags, **options)


def _logs_line(folder: Path) -> str:
    logs = folder / LOGS
    return (f"its lines are in {logs / 'start.log'}, the output of each part in {logs} (runner.out, review.out, capture.out); "
            "see how they are with --status, stop them with --stop")


def background(settings_file: Path, testshop: bool, poll_seconds: float | None, python, spawn=subprocess.Popen, *, sleep=time.sleep,
               clock=time.monotonic, wall=time.time, opener=None) -> int:
    """--background: the checks of a start here, where their line is read (a leftover state file is removed here, so that the
    launcher does not watch it again), then the launcher started apart from this window; 0, 3 when a check refuses the start, 1
    when the launcher cannot be started."""
    folder = Path(settings_file).resolve().parent
    log = _Log(folder, wall)
    if _refusal(folder, log, sleep, clock, wall, opener, detached=True) is not None:
        return 3
    try:
        start_detached(_launcher(settings_file, testshop, python, poll_seconds), spawn)
    except OSError as exc:
        log(f"the launcher could not be started in the background ({type(exc).__name__}: {exc})")
        return 1
    _print(f"the launcher is started in the background; {_logs_line(folder)}")
    return 0


def _make_shortcut(values: dict, run) -> str | None:
    """Make the shortcut through PowerShell; None when it is made, else why not. A script that ends with a code other than 0 made
    no shortcut, or deleted the one that did not hold the values it was given."""
    env = {**os.environ, "AID_LNK": str(values["path"]), "AID_TARGET": values["target"], "AID_ARGS": values["arguments"],
           "AID_CWD": values["working_folder"]}
    try:
        done = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SCRIPT], env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, errors="replace", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return type(exc).__name__
    if done.returncode != 0:
        return f"exit code {done.returncode}"
    if not Path(values["path"]).exists():
        return "no shortcut was made"
    return None


def _write_cmd(values: dict, path: Path) -> None:
    """The fallback: a .cmd file that starts the launcher as the shortcut would. No character is ever replaced: the file is ASCII
    when every character is, else UTF-8 without a byte order mark, with the code page set in its second line. pushd, not cd, so
    that a working folder on a network path is taken. A character that cannot be written is a UnicodeError, and nothing is written."""
    def escaped(text: str) -> str:
        return text.replace("%", "%%")
    lines = ["@echo off",
             "rem The start at logon of the checking aid, written by: python -m aid.start --install-autostart",
             f'pushd "{escaped(values["working_folder"])}"',
             f'start "" "{escaped(values["target"])}" {escaped(values["arguments"])}']
    ascii_only = all(line.isascii() for line in lines)
    if not ascii_only:
        lines.insert(1, "chcp 65001 >nul")
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii" if ascii_only else "utf-8"))


def _remove(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def install_autostart(settings_file: Path, testshop: bool, poll_seconds: float | None, python, run=subprocess.run,
                      spawn=subprocess.Popen, wall=time.time, *, sleep=time.sleep, clock=time.monotonic, opener=None,
                      startup: Path | None = None) -> int:
    """Make the start at logon (the shortcut, else the .cmd file) and start the launcher as --background does, after the same
    checks. 0 when the start at logon is set, 1 when neither file could be made (the launcher is then not started either, so
    that nothing hides it until the next logon)."""
    values = autostart_shortcut(settings_file, testshop, python, poll_seconds, startup=startup)
    link = Path(values["path"])
    cmd = link.with_suffix(".cmd")
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        why = _make_shortcut(values, run)
    except OSError as exc:                       # the Startup folder cannot be made
        why = type(exc).__name__
    if why is None:
        made, other = link, cmd
        _print(f"The start at logon is set: the shortcut {link}")
    else:
        try:
            _write_cmd(values, cmd)
        except (OSError, UnicodeError) as exc:
            made, other = None, link
            _print(f"The start at logon could not be set: PowerShell could not make the shortcut ({why}) and {cmd} could not be "
                   f"written ({type(exc).__name__})")
        else:
            made, other = cmd, link
            _print(f"PowerShell could not make the shortcut ({why}); the start at logon is set with {cmd} instead")
    try:                                         # the file of an earlier time, or a shortcut left half made or with a lost character
        _remove(other)
    except OSError as exc:
        if made is not None:
            _print(f"{other} could not be removed ({type(exc).__name__}): delete it, or the launcher is started twice at logon")
    _print(f"It runs: {values['target']} {values['arguments']}  (in {values['working_folder']})")    # also for a shortcut by hand
    if made is None:
        return 1
    folder = Path(settings_file).resolve().parent
    refused = _refusal(folder, _Log(folder, wall), sleep, clock, wall, opener, detached=True)
    if refused == "stop":
        _print("The launcher was not started now; start it with --resume --background")
    elif refused == "running":
        _print("A launcher is already running on these settings; it was not started a second time")
    elif refused == "answers":
        _print("The start at logon is set, but the launcher was not started now: once nothing answers there any more, run "
               "--resume --background, or log on again")
    else:
        try:
            start_detached(values, spawn)
        except OSError as exc:
            _print(f"The launcher could not be started now ({type(exc).__name__}: {exc}); it starts at the next logon")
        else:
            _print("The launcher is started in the background now")
    line = _logs_line(folder)
    _print(line[:1].upper() + line[1:])
    _print(("After --resume --background, log off" if refused == "stop" else "Log off")
           + " and on once, then check with --status that the launcher started")
    return 0


def remove_autostart(startup: Path | None = None) -> int:
    """Delete the shortcut and the .cmd file of the start at logon; 1 when one of them cannot be deleted."""
    folder = Path(startup) if startup is not None else startup_folder()
    removed, kept = [], []
    for path in (folder / f"{SHORTCUT}.lnk", folder / f"{SHORTCUT}.cmd"):
        try:
            if _remove(path):
                removed.append(str(path))
        except OSError as exc:
            kept.append(f"{path} ({type(exc).__name__})")
    if removed:
        _print(f"Removed: {', '.join(removed)}")
    elif not kept:
        _print(f"No start at logon was set ({folder})")
    if kept:
        _print(f"Could not be removed: {', '.join(kept)}; delete it by hand, or the launcher still starts at logon")
    _print("A launcher that is running goes on until --stop")
    return 1 if kept else 0


# --- the command ---

def _settings_problem(settings_file: Path) -> str | None:
    """The line that says why the settings file is not used, by the fields and never a value; None when it is. A file in which
    exact_base_url is set can be read and is refused, with the one sentence of aid/settings.py: no command builds a write route
    into Exact."""
    try:
        load_settings(settings_file)
    except OSError as exc:
        return f"the settings cannot be read: {settings_file}: {type(exc).__name__}"
    except WriteRouteSet as exc:
        return f"the settings are refused: {exc}"
    except ValueError as exc:                    # load_settings names the fields, never the values; a JSON error its position
        return f"the settings cannot be read: {exc}"
    return None


def _command(args, settings: Path, log: _Log) -> int:
    if args.status or args.stop:
        if not settings.is_file():               # before anything is written: no STOP file and no log in a folder that is not the aid's
            _print(f"the settings file does not exist: {settings}")
            return 2
        return status(settings) if args.status else stop(settings)
    if (args.install_autostart or args.remove_autostart) and not _windows():
        _print("--install-autostart and --remove-autostart are for Windows; on another system start the launcher by the system's own means")
        return 2
    if args.remove_autostart:
        return remove_autostart()
    if not settings.is_file():                   # as --status says it, and nothing is written in a folder that may not be the aid's
        _print(f"the settings file does not exist: {settings}")
        return 2
    problem = _settings_problem(settings)
    if problem is not None:
        log(problem)                             # to stdout, and to start.log when its folder can be made
        return 2
    if args.install_autostart:
        return install_autostart(settings, args.testshop, args.poll_seconds, sys.executable)
    if args.resume:
        refused = resume(settings)
        if refused is not None:
            return refused
    if args.background:
        return background(settings, args.testshop, args.poll_seconds, sys.executable)
    return supervise(settings, commands(settings, args.testshop, args.poll_seconds, sys.executable), poll_seconds=args.poll_seconds,
                     window=True, ignore_after_stop=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aid.start",
                                     description="Start the runner, the review page and the capture server together and keep them running.")
    parser.add_argument("--settings", type=Path, required=True, help="the settings file")
    parser.add_argument("--testshop", action="store_true", help="the capture server also serves the made-up cart pages")
    parser.add_argument("--poll-seconds", type=float, default=None, help="the runner's seconds between passes (default its own, 60)")
    parser.add_argument("--background", action="store_true",
                        help="for a start and for --resume: start the launcher without a window and return")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true", help="how the launcher and the parts are")
    action.add_argument("--stop", action="store_true", help="create STOP and wait for the parts to end")
    action.add_argument("--resume", action="store_true", help="delete STOP and start")
    action.add_argument("--install-autostart", action="store_true", help="Windows: start at logon, and start now")
    action.add_argument("--remove-autostart", action="store_true", help="Windows: no longer start at logon")
    args = parser.parse_args(argv)
    if args.poll_seconds is not None and not (math.isfinite(args.poll_seconds) and args.poll_seconds > 0):
        _print("--poll-seconds must be a number above 0")
        return 2
    settings = args.settings.resolve()
    log = _Log(settings.parent)
    try:
        return _command(args, settings, log)
    except KeyboardInterrupt:                    # Ctrl-C outside the supervision (which has its own): the watch, the waits of --stop and
        _print("Ctrl-C: " + ("the STOP file stays, and the launcher goes on stopping the parts; check with --status" if args.stop
                             else "nothing was started"))   # of --resume; one line of what stands, and no traceback
        return 130
    except Exception as exc:                     # a fault outside the supervision: under pythonw no window shows it
        log.fault(exc, f"the launcher failed ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
