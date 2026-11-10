"""The launcher of the three parts (aid/start.py): the child commands, the supervision with a fake spawn, clock and sleep, the stop
switch and the launcher's own stop, the signals, the single instance and the leftovers, --status, --stop, --resume, --background,
the tie to the launcher and the start at logon on Windows (with fakes), and two runs of the real launcher on the demo folder."""
import ctypes
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

import pytest

from aid import start
from aid.tests.conftest import REPO_ROOT

PY = "/opt/py/bin/python3"
REVIEW, CAPTURE = "http://127.0.0.1:7860/", "http://127.0.0.1:7870/health"
STOP_SIGNALS = [getattr(signal, name) for name in ("SIGTERM", "SIGHUP", "SIGQUIT", "SIGBREAK") if hasattr(signal, name)]
# the creation flags of Windows by their numbers: DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, CREATE_BREAKAWAY_FROM_JOB
DETACHED, NO_WINDOW, BREAKAWAY = 0x8 | 0x200, 0x08000000, 0x01000000
WINDOW = "the launcher runs in this window: closing the window stops the parts (use --background to run it without a window)"
ONE_LAUNCHER = ("only one launcher runs on a PC; a launcher on other settings is stopped with --stop on its own settings, "
                "and parts started by hand are closed")
WATCHING = "a launcher may still run on these settings: watching its heartbeat for 7 seconds"


# --- fakes ---

class Clock:
    """A clock whose time moves only when the fake sleep is called; hooks run at given times (to create STOP, for example)."""

    def __init__(self, now: float = 1000.0):
        self.now = now
        self.slept = 0
        self.hooks: list[tuple[float, object]] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += 1
        if self.slept > 100_000:
            raise AssertionError("the launcher does not end")
        self.now += seconds
        for when, hook in list(self.hooks):
            if self.now >= when:
                self.hooks.remove((when, hook))
                hook()


class CreepingClock(Clock):
    """A clock that moves on by a microsecond at each reading, as a real one does between two readings of a program, and whose
    sleep wakes exactly when the program asked: at its last reading plus the seconds."""

    def __init__(self, now: float = 1000.0):
        super().__init__(now)
        self.read = now

    def __call__(self) -> float:
        self.read = self.now
        self.now += 1e-6
        return self.read

    def sleep(self, seconds: float) -> None:
        self.now = self.read
        super().sleep(seconds)


class Proc:
    """A child process whose end is scripted: it ends with code after lifetime seconds of the clock; terminate() ends it with -15
    unless it ignores it; kill() ends it, dies_after_kill seconds later. It keeps when it was terminated and killed."""

    pids = 100

    def __init__(self, clock: Clock, lifetime: float = float("inf"), code: int = 0, ignores_terminate: bool = False,
                 dies_after_kill: float = 0.0):
        Proc.pids += 1
        self.pid = Proc.pids
        self._handle = 0x1_0000_0000 + self.pid               # wider than 32 bits, as a handle of a 64-bit Windows may be
        self.clock, self.started = clock, clock.now
        self.lifetime, self.code, self.ignores_terminate, self.dies_after_kill = lifetime, code, ignores_terminate, dies_after_kill
        self.returncode = None
        self.terminated = self.killed = 0
        self.terminated_at = self.killed_at = None
        self.on_kill = None

    def poll(self):
        if self.returncode is None:
            if self.killed_at is not None and self.clock.now - self.killed_at >= self.dies_after_kill:
                self.returncode = -9
            elif self.clock.now - self.started >= self.lifetime:
                self.returncode = self.code
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()

    def terminate(self):
        self.terminated += 1
        if self.terminated_at is None:
            self.terminated_at = self.clock.now
        if not self.ignores_terminate and self.returncode is None:
            self.returncode = -15

    def kill(self):
        self.killed += 1
        if self.killed_at is None:
            self.killed_at = self.clock.now
        if self.on_kill is not None:
            self.on_kill()
        self.poll()


class ObedientRunner(Proc):
    """A runner as aid.runner is: it ends by itself, with 0, at its next pass after the STOP file appeared (after seconds), and
    keeps the text the file then held."""

    def __init__(self, clock: Clock, stop_file: Path, after: float = 3.0):
        super().__init__(clock)
        self.stop_file, self.after = stop_file, after
        self.stop_seen = self.stop_text = None

    def poll(self):
        if self.returncode is None and self.stop_seen is None and self.stop_file.exists():
            self.stop_seen, self.stop_text = self.clock.now, self.stop_file.read_text(encoding="utf-8")
        if self.returncode is None and self.stop_seen is not None and self.clock.now - self.stop_seen >= self.after:
            self.returncode = 0
        return super().poll()


class Spawn:
    """A fake subprocess.Popen: per part (found by its module in the command) a list of (lifetime, code) for its successive starts,
    the last one repeated; a part without a script runs until it is stopped; makers gives a part's process another way."""

    def __init__(self, clock: Clock, scripts: dict | None = None, ignores_terminate: tuple = (), makers: dict | None = None):
        self.clock, self.scripts, self.ignores, self.makers = clock, scripts or {}, ignores_terminate, makers or {}
        self.calls: list[tuple[str, float, list, dict]] = []
        self.procs: dict[str, list[Proc]] = {}
        self.order: list = []                                  # every process in the order of its start

    def __call__(self, cmd, **kwargs):
        name = {"aid.runner": "runner", "aid.review": "review", "aid.capture_server": "capture"}[cmd[cmd.index("-m") + 1]]
        self.calls.append((name, self.clock.now, cmd, kwargs))
        if name in self.makers:
            proc = self.makers[name]()
        else:
            script = self.scripts.get(name, [(float("inf"), 0)])
            lifetime, code = script[min(len(self.procs.get(name, [])), len(script) - 1)]
            proc = Proc(self.clock, lifetime, code, name in self.ignores)
        self.procs.setdefault(name, []).append(proc)
        self.order.append(proc)
        return proc

    def times(self, name: str) -> list[float]:
        return [when for part, when, _, _ in self.calls if part == name]


class Answer:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def refused(url, timeout):
    raise urllib.error.URLError("refused")


def answering(*ports: int):
    """An opener for which the addresses on these ports answer and the others refuse; it keeps what was asked."""
    def opener(url, timeout):
        opener.asked.append((url, timeout))
        if any(f":{port}/" in url for port in ports):
            return Answer()
        raise urllib.error.URLError("refused")
    opener.asked = []
    return opener


def no_tie(proc) -> None:
    """The tie of the tests that are not about it: the parts are not tied to the launcher, as off Windows."""


def tying(proc) -> bool:
    """A tie that takes every process, as the job object of Windows does."""
    return True


@pytest.fixture(autouse=True)
def nothing_answers(monkeypatch):
    """No test asks a real address: the review page and the capture server refuse unless a test gives its own opener."""
    monkeypatch.setattr(start, "_open_url", refused)


@pytest.fixture
def windows_numbers(monkeypatch):
    """subprocess as on Windows, which has these three creation flags (elsewhere it has none, and the launcher's getattr gives 0)."""
    for name, value in (("DETACHED_PROCESS", 0x8), ("CREATE_NEW_PROCESS_GROUP", 0x200), ("CREATE_NO_WINDOW", NO_WINDOW)):
        monkeypatch.setattr(subprocess, name, value, raising=False)


@pytest.fixture(autouse=True)
def handlers_of_a_program_started_from_a_terminal():
    """Every test starts with the handlers a program has when it is started from a terminal, whatever the test run inherited
    (a run in the background may have Ctrl-C ignored), and leaves the handlers it found."""
    wanted = {number: signal.SIG_DFL for number in STOP_SIGNALS} | {signal.SIGINT: signal.default_int_handler}
    earlier = {number: signal.signal(number, handler) for number, handler in wanted.items()}
    yield
    for number, handler in earlier.items():
        signal.signal(number, handler if handler is not None else signal.SIG_DFL)


@pytest.fixture
def folder(tmp_path):
    """A folder with a settings file (its content is not read by supervise)."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    return tmp_path


def example_settings(folder: Path) -> Path:
    (folder / "settings.json").write_text((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    return folder / "settings.json"


def parts(folder: Path, poll: float | None = None) -> dict[str, list[str]]:
    return start.commands(folder / "settings.json", False, poll, PY)


def run(folder: Path, clock: Clock, spawn, poll: float | None = None, **more) -> int:
    more.setdefault("sleep", clock.sleep)
    more.setdefault("tie", no_tie)
    try:
        return start.supervise(folder / "settings.json", parts(folder, poll), spawn=spawn, clock=clock, wall=clock, poll_seconds=poll,
                               **more)
    except KeyboardInterrupt:                                  # pytest would take it for its own interruption and end the whole run
        pytest.fail("the launcher ended with a KeyboardInterrupt")


def log(folder: Path) -> str:
    return (folder / "logs" / "start.log").read_text(encoding="utf-8")


def stop_at(clock: Clock, folder: Path, when: float) -> None:
    clock.hooks.append((when, lambda: (folder / "STOP").write_text("", encoding="utf-8")))


def part(pid, state: str = "running", command: str = "aid.runner", starts: int = 1, tied: bool | None = None) -> dict:
    """A part's record in a state file; tied None: a record of before this round, without the word."""
    record = {"command": command, "pid": pid, "started": "2026-11-10T08:00:01", "starts": starts, "given_up": state == "given up",
              "state": state}
    if tied is not None:
        record["tied"] = tied
    return record


def write_state(folder: Path, heartbeat: float, parts: dict | None = None, heartbeat_at: str = "2026-11-10T08:35:49",
                runner_poll: float | None = None) -> None:
    launcher = {"pid": 7, "started": "2026-11-10T08:00:00", "heartbeat": heartbeat, "heartbeat_at": heartbeat_at}
    if runner_poll is not None:
        launcher["runner_poll_seconds"] = runner_poll
    (folder / start.STATE_FILE).write_text(json.dumps({"launcher": launcher, "parts": parts or {}}), encoding="utf-8")


def read_state(folder: Path) -> dict:
    return json.loads((folder / start.STATE_FILE).read_text(encoding="utf-8"))


def beating(clock: Clock, folder: Path, seconds: float, **state) -> None:
    """A launcher that runs on this folder for the next seconds: its heartbeat is written now and every 5 seconds of the clock."""
    def beat():
        write_state(folder, clock.now, **state)
    beat()
    for step in range(5, int(seconds) + 1, 5):
        clock.hooks.append((clock.now + step, beat))


def interrupted_at(clock: Clock, *calls: int):
    """A sleep that raises KeyboardInterrupt at these calls, as Python does for a Ctrl-C it handles itself."""
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        if count["n"] in calls:
            raise KeyboardInterrupt
        clock.sleep(seconds)
    return sleep


def signalled_at(clock: Clock, plan: dict):
    """A sleep at the end of which this process gets a real signal (or several) at the given calls, as from the keyboard or the
    system. A signal without a handler would end the whole test run, so the test fails instead."""
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        clock.sleep(seconds)
        numbers = plan.get(count["n"], ())
        for number in numbers if isinstance(numbers, tuple) else (numbers,):
            send(number)
    return sleep


def send(number) -> None:
    handler = signal.getsignal(number)
    if handler is not signal.SIG_IGN and not callable(handler):
        pytest.fail(f"no handler for {signal.Signals(number).name} while the launcher runs")
    signal.raise_signal(number)


def handlers() -> dict:
    return {number: signal.getsignal(number) for number in (signal.SIGINT, *STOP_SIGNALS)}


# --- the commands and how they are started ---

def test_the_three_commands_use_the_given_python_and_the_absolute_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "live").mkdir()
    settings = Path("live/settings.json")
    absolute = str((tmp_path / "live" / "settings.json").resolve())
    assert start.commands(settings, False, None, PY) == {
        "runner": [PY, "-m", "aid.runner", "--settings", absolute],
        "review": [PY, "-m", "aid.review", "--settings", absolute],
        "capture": [PY, "-m", "aid.capture_server", "--settings", absolute]}
    with_options = start.commands(settings, True, 5, PY)
    assert with_options["runner"] == [PY, "-m", "aid.runner", "--settings", absolute, "--poll-seconds", "5"]
    assert with_options["review"] == [PY, "-m", "aid.review", "--settings", absolute]
    assert with_options["capture"] == [PY, "-m", "aid.capture_server", "--settings", absolute, "--testshop"]
    assert start.commands(settings, False, 1.5, PY)["runner"][-2:] == ["--poll-seconds", "1.5"]


def test_the_stop_file_is_the_runners_own():
    from aid.runner import STOP_FILE
    assert start.STOP_FILE == STOP_FILE


def test_each_part_starts_from_the_packages_folder_with_its_output_appended_to_its_log_file(folder, monkeypatch, windows_numbers):
    monkeypatch.setattr(start, "_windows", lambda: False)
    monkeypatch.setenv("AID_TEST_MARK", "the launcher's own environment")
    monkeypatch.setenv("PYTHONUNBUFFERED", "")                 # other values first: the launcher must set its own
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    clock = Clock()
    spawn = Spawn(clock)
    (folder / "logs").mkdir()
    (folder / "logs" / "review.out").write_text("earlier\n", encoding="utf-8")
    stop_at(clock, folder, clock.now + 3)
    assert run(folder, clock, spawn) == 0
    assert [name for name, *_ in spawn.calls] == ["runner", "review", "capture"]
    for name, _, cmd, kwargs in spawn.calls:
        assert kwargs["cwd"] == str(REPO_ROOT)
        assert (Path(kwargs["cwd"]) / "aid" / "start.py").is_file()
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.STDOUT
        assert Path(kwargs["stdout"].name) == folder / "logs" / f"{name}.out"
        assert "a" in kwargs["stdout"].mode
        assert kwargs["stdout"].closed                     # the launcher keeps no handle of the child's file
        assert kwargs["creationflags"] == NO_WINDOW         # CREATE_NO_WINDOW, as subprocess has it on Windows
        assert kwargs["start_new_session"] is True         # a Ctrl-C or a hangup of the terminal reaches the launcher only
        # the launcher's own environment, and every line of the .out file at once and in one encoding
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1" and kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert kwargs["env"] == {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        assert kwargs["env"]["AID_TEST_MARK"] == "the launcher's own environment"
    assert (folder / "logs" / "review.out").read_text(encoding="utf-8") == "earlier\n"
    text = log(folder)
    assert "started runner" in text and "started review" in text and "started capture" in text
    assert text.splitlines()[0][:4].isdigit()               # each line begins with the time


def test_on_windows_the_parts_get_no_session_of_their_own(folder, monkeypatch, windows_numbers):
    monkeypatch.setattr(start, "_windows", lambda: True)
    clock = Clock()
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 2)
    assert run(folder, clock, spawn) == 0
    assert [kwargs["start_new_session"] for _, _, _, kwargs in spawn.calls] == [False, False, False]
    assert [kwargs["creationflags"] for _, _, _, kwargs in spawn.calls] == [NO_WINDOW] * 3    # no console window per part


def test_the_launcher_writes_no_line_to_a_missing_stdout(folder, monkeypatch, capsys):
    """Under pythonw sys.stdout is None: the lines go to start.log only."""
    monkeypatch.setattr(sys, "stdout", None)
    clock = Clock()
    stop_at(clock, folder, clock.now + 2)
    assert run(folder, clock, Spawn(clock), window=True) == 0
    assert "stopped" in log(folder)


def test_a_start_in_a_window_says_first_that_closing_the_window_stops_the_parts(folder, capsys):
    clock = Clock()
    stop_at(clock, folder, clock.now + 2)
    assert run(folder, clock, Spawn(clock), window=True) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == WINDOW
    assert lines[1].startswith("started runner")
    assert "this window" not in log(folder)                  # for the person at the window, not a line of the log


def test_a_start_that_is_refused_or_not_in_a_window_does_not_say_so(folder, capsys):
    clock = Clock()
    stop_at(clock, folder, clock.now + 2)
    assert run(folder, clock, Spawn(clock)) == 0
    assert "this window" not in capsys.readouterr().out
    assert run(folder, clock, Spawn(clock), window=True) == 3   # STOP is still there
    assert "this window" not in capsys.readouterr().out


# --- supervision ---

def test_a_part_that_ends_is_started_again_after_five_seconds(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(10, 1), (float("inf"), 0)]})
    stop_at(clock, folder, clock.now + 60)
    assert run(folder, clock, spawn) == 0
    first, second = spawn.times("capture")
    assert 15 <= second - first <= 17                        # ended after 10 seconds, started again 5 seconds later
    assert len(spawn.times("runner")) == 1 and len(spawn.times("review")) == 1
    text = log(folder)
    assert "capture ended with exit code 1" in text and "started capture again" in text


def test_a_part_that_keeps_ending_is_given_up_after_five_restarts_in_ten_minutes_and_the_others_keep_running(folder):
    clock = Clock()
    spawn = Spawn(clock, {"review": [(1, 1)]})
    settings_saved_at(clock, folder, 100)                   # a part that is given up stays so, whatever the settings file does
    stop_at(clock, folder, clock.now + 300)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.times("review")) == 6                  # the first start and five restarts
    assert len(spawn.times("runner")) == 1 and len(spawn.times("capture")) == 1
    assert spawn.procs["runner"][0].returncode is not None   # the runner ran on until the stop
    text = log(folder)
    assert "review given up" in text and "read logs/review.out" in text


@pytest.mark.parametrize("seconds", [100, 115])
def test_a_part_that_ends_every_100_seconds_is_given_up_at_its_sixth_end(folder, seconds):
    """Five restarts 105 seconds apart lie within ten minutes; so do, just, five that are 120 seconds apart (115 and the 5 of the
    wait): at the sixth end the first of them is 595 seconds old."""
    clock = Clock()
    spawn = Spawn(clock, {"review": [(seconds, 1)]})
    stop_at(clock, folder, clock.now + 2000)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.times("review")) == 6
    assert "review given up" in log(folder)
    assert spawn.procs["review"][5].started + seconds - 1000 == 1 + 6 * seconds + 25  # its sixth end (1: the claim's tick)


@pytest.mark.parametrize("seconds", [116, 130])
def test_a_part_that_ends_every_130_seconds_is_never_given_up(folder, seconds):
    """Of restarts 135 seconds apart only four lie within any ten minutes; of restarts 121 seconds apart too: at the sixth end
    the first of five is 600 seconds old."""
    clock = Clock()
    spawn = Spawn(clock, {"runner": [(seconds, 1)]})
    stop_at(clock, folder, clock.now + 3000)
    assert run(folder, clock, spawn, poll=1) == 0
    assert len(spawn.times("runner")) >= 20
    assert "given up" not in log(folder)


def test_a_part_that_ends_with_code_2_is_not_started_again(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(2, 2)]})
    stop_at(clock, folder, clock.now + 60)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.times("capture")) == 1
    assert len(spawn.times("runner")) == 1 and len(spawn.times("review")) == 1
    assert "capture ended with exit code 2" in log(folder) and "read logs/capture.out" in log(folder)


def settings_saved_at(clock: Clock, folder: Path, *seconds: float) -> None:
    """The settings file gets a new modification time at these seconds from now, as when the settings page saves it."""
    settings = folder / "settings.json"
    base = os.stat(settings).st_mtime_ns

    def save(step: int):
        os.utime(settings, ns=(base + step * 10**9, base + step * 10**9))
    for step, second in enumerate(seconds, start=1):
        clock.hooks.append((clock.now + second, lambda step=step: save(step)))


def test_a_part_that_ended_with_code_2_is_started_again_when_the_settings_file_has_changed(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(2, 2), (float("inf"), 0)]})
    settings_saved_at(clock, folder, 40)
    stop_at(clock, folder, clock.now + 60)
    assert run(folder, clock, spawn) == 0
    first, second = spawn.times("capture")
    assert 40 <= second - 1000 <= 41                         # at the look after the change, not before and not without one
    assert len(spawn.times("runner")) == 1 and len(spawn.times("review")) == 1
    assert "the settings file has changed: starting capture again" in log(folder)


def test_a_part_that_is_refused_again_waits_for_the_next_change_of_the_settings_file(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(2, 2)]})
    settings_saved_at(clock, folder, 40)
    stop_at(clock, folder, clock.now + 200)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.times("capture")) == 2                   # one start for the one change, not one at every look


def test_a_start_after_a_changed_settings_file_is_not_one_of_the_five_restarts(folder):
    """Two starts after a change of the settings, then five restarts in ten minutes before the part is given up."""
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(2, 2), (2, 2), (1, 1)]})
    settings_saved_at(clock, folder, 30, 60)
    stop_at(clock, folder, clock.now + 300)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.times("capture")) == 3 + 5
    assert "capture given up" in log(folder)


def test_the_launcher_ends_with_1_when_every_part_has_ended_or_been_given_up(folder):
    clock = Clock()
    spawn = Spawn(clock, {"runner": [(1, 2)], "review": [(1, 1)], "capture": [(3, 2)]})
    settings_saved_at(clock, folder, 500)                     # a change after the launcher has ended brings nothing back
    assert run(folder, clock, spawn) == 1
    assert len(spawn.times("review")) == 6
    assert len(spawn.times("runner")) == 1 and len(spawn.times("capture")) == 1
    assert not (folder / start.STATE_FILE).exists()
    assert "every part has ended" in log(folder)


def test_a_part_that_cannot_be_started_counts_as_ended_and_its_line_gives_the_error_itself(folder):
    clock = Clock()

    def spawn(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file", cmd[0])
    assert run(folder, clock, spawn) == 1
    text = log(folder)
    assert f"runner could not be started (FileNotFoundError: [Errno 2] No such file: '{PY}')" in text
    assert "given up" in text
    assert ".out" not in text                                 # "read its .out file" is for a part that ran


# --- the tie of the parts to the launcher (Windows) ---

def test_every_started_process_is_given_to_the_tie_right_after_its_start_a_restarted_one_too(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(10, 1), (float("inf"), 0)]})
    tied = []

    def tie(proc):
        tied.append((proc, len(spawn.order)))
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn, tie=tie) == 0
    assert len(spawn.order) == 4                              # the three parts and the capture server once more
    assert [proc for proc, _ in tied] == spawn.order
    assert [started for _, started in tied] == [1, 2, 3, 4]   # each before the next process is started
    assert "could not be tied" not in log(folder)


def test_a_failing_tie_is_logged_once_and_changes_nothing_else(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(10, 1), (float("inf"), 0)]})
    tied = []

    def tie(proc):
        tied.append(proc)
        raise OSError("AssignProcessToJobObject failed with error 5")
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn, tie=tie) == 0
    text = log(folder)
    assert text.count("could not be tied") == 1
    assert ("the parts could not be tied to the launcher (OSError: AssignProcessToJobObject failed with error 5): "
            "if the launcher is ended from outside, close the parts by hand") in text
    assert tied == spawn.order and len(spawn.order) == 4       # every part was started, and started again, as without the failure
    assert all(proc.returncode is not None for proc in spawn.order)
    assert text.rstrip().endswith("stopped") and not (folder / start.STATE_FILE).exists()


@pytest.mark.parametrize("tie, tied", [(tying, True), (no_tie, False), ("fails", False)], ids=["tied", "not tied", "the tie fails"])
def test_the_state_says_of_each_part_whether_the_tie_took_its_process(folder, tie, tied):
    """So that --status and a later start know whether Windows ended the part with the launcher."""
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(3, 1), (float("inf"), 0)]})

    def failing(proc):
        raise OSError("AssignProcessToJobObject failed with error 5")
    seen = {}
    clock.hooks.append((clock.now + 12, lambda: seen.update(read_state(folder)["parts"])))   # the capture server started again too
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, spawn, tie=failing if tie == "fails" else tie) == 0
    assert [seen[name]["tied"] for name in ("runner", "review", "capture")] == [tied] * 3
    assert seen["capture"]["starts"] == 2


def test_the_job_objects_structure_has_the_size_windows_expects():
    basic = start.JOBOBJECT_BASIC_LIMIT_INFORMATION
    assert [getattr(basic, name).size for name in ("LimitFlags", "ActiveProcessLimit", "PriorityClass", "SchedulingClass")] == [4] * 4
    assert basic.PerProcessUserTimeLimit.size == 8 and basic.Affinity.size == ctypes.sizeof(ctypes.c_void_p)
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(start.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144
        assert ctypes.sizeof(start.JOBOBJECT_BASIC_LIMIT_INFORMATION) == 64 and ctypes.sizeof(start.IO_COUNTERS) == 48
        assert start.JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags.offset == 16
    elif os.name == "nt":                                     # elsewhere a 32-bit system aligns a 64-bit number differently
        assert ctypes.sizeof(start.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 112


class Call:
    """A function of a fake kernel32: it keeps the types set on it and the calls made, and gives its result."""

    def __init__(self, result):
        self.result, self.calls, self.argtypes, self.restype = result, [], None, None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


JOB = 0x2_0000_0044                                           # a job handle wider than 32 bits


def fake_kernel32(monkeypatch, **results):
    """Windows for the tie: start._windows says so, and ctypes.WinDLL gives a kernel32 whose three calls give these results."""
    kernel32 = types.SimpleNamespace(CreateJobObjectW=Call(results.get("CreateJobObjectW", JOB)),
                                     SetInformationJobObject=Call(results.get("SetInformationJobObject", 1)),
                                     AssignProcessToJobObject=Call(results.get("AssignProcessToJobObject", 1)), loaded=[])

    def windll(name, **kwargs):
        kernel32.loaded.append((name, kwargs))
        return kernel32
    monkeypatch.setattr(ctypes, "WinDLL", windll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(start, "_windows", lambda: True)
    return kernel32


def test_the_tie_puts_each_process_in_one_job_object_that_windows_ends_with_the_launcher(monkeypatch):
    kernel32 = fake_kernel32(monkeypatch)
    tie = start._Job()
    first, second = Proc(Clock()), Proc(Clock())
    assert tie(first) is True and tie(second) is True                          # each process is in the job: tied
    assert kernel32.loaded == [("kernel32", {"use_last_error": True})]
    assert kernel32.CreateJobObjectW.calls == [(None, None)]                   # one job object, made once
    (job, kind, reference, size), = kernel32.SetInformationJobObject.calls
    info = reference._obj
    assert (job, kind, size) == (JOB, 9, ctypes.sizeof(start.JOBOBJECT_EXTENDED_LIMIT_INFORMATION))
    assert isinstance(info, start.JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK: what a part starts (a browser for a sign-in) is not
    # in the job, so it is not ended with the launcher
    assert info.BasicLimitInformation.LimitFlags == 0x2000 | 0x1000 == 0x3000
    assert kernel32.AssignProcessToJobObject.calls == [(JOB, first._handle), (JOB, second._handle)]
    # explicit types, a handle as wide as a pointer: a 64-bit handle is not cut
    assert kernel32.CreateJobObjectW.argtypes == [wintypes.LPVOID, wintypes.LPCWSTR]
    assert kernel32.CreateJobObjectW.restype is wintypes.HANDLE
    assert kernel32.SetInformationJobObject.argtypes == [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    assert kernel32.SetInformationJobObject.restype is wintypes.BOOL
    assert kernel32.AssignProcessToJobObject.argtypes == [wintypes.HANDLE, wintypes.HANDLE]
    assert kernel32.AssignProcessToJobObject.restype is wintypes.BOOL
    assert not hasattr(kernel32, "CloseHandle")                                # the launcher never closes the job's handle
    assert tie.handle == JOB                                                   # and keeps it for its whole life


@pytest.mark.parametrize("failing", ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"])
def test_a_call_on_the_job_object_with_a_zero_result_is_an_error_that_names_the_call(monkeypatch, failing):
    kernel32 = fake_kernel32(monkeypatch, **{failing: None if failing == "CreateJobObjectW" else 0})
    tie = start._Job()
    with pytest.raises(OSError, match=f"{failing} failed with error 5"):
        tie(Proc(Clock()))
    with pytest.raises(OSError):
        tie(Proc(Clock()))
    assert len(kernel32.CreateJobObjectW.calls) == 1            # a job object is not made a second time


def test_the_real_tie_that_fails_writes_one_line_and_the_launcher_goes_on(folder, monkeypatch):
    fake_kernel32(monkeypatch, AssignProcessToJobObject=0)
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(10, 1), (float("inf"), 0)]})
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn, tie=None) == 0             # the launcher's own tie
    text = log(folder)
    assert text.count("the parts could not be tied to the launcher (OSError: AssignProcessToJobObject failed with error 5)") == 1
    assert len(spawn.order) == 4 and text.rstrip().endswith("stopped")


def test_the_real_tie_fails_safe_where_windows_lacks_what_it_needs(folder, monkeypatch):
    """A kernel32 that cannot be loaded (here: no ctypes.WinDLL at all) is one line in the log, and the launcher goes on."""
    monkeypatch.setattr(start, "_windows", lambda: True)
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    clock = Clock()
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 5)
    assert run(folder, clock, spawn, tie=None) == 0
    text = log(folder)
    assert text.count("the parts could not be tied to the launcher (AttributeError") == 1
    assert len(spawn.order) == 3


def test_off_windows_the_tie_does_nothing(folder, monkeypatch):
    monkeypatch.setattr(start, "_windows", lambda: False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: pytest.fail("kernel32 was asked off Windows"), raising=False)
    assert start._Job()(Proc(Clock())) is False                                # not tied: the launcher stops its parts itself
    clock = Clock()
    stop_at(clock, folder, clock.now + 2)
    assert run(folder, clock, Spawn(clock), tie=None) == 0
    assert "tied" not in log(folder)


# --- the stop switch ---

def test_stop_at_the_start_starts_nothing(folder, capsys):
    (folder / "STOP").write_text("", encoding="utf-8")
    clock = Clock()
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == []
    assert "stopped by the STOP file: delete it, or use --resume" in log(folder)
    assert "stopped by the STOP file" in capsys.readouterr().out
    assert not (folder / start.STATE_FILE).exists()
    assert clock.slept == 0


@pytest.mark.parametrize("text", ["", "stop", "Launcher 2026-11-10T08:43:09", " launcher 2026-11-10T08:43:09", "launcher"])
def test_every_stop_file_that_is_not_the_launchers_own_is_obeyed(folder, text):
    (folder / "STOP").write_text(text, encoding="utf-8")
    clock = Clock()
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == [] and (folder / "STOP").read_text(encoding="utf-8") == text
    assert "stopped by the STOP file" in log(folder)


def test_a_start_removes_a_stop_file_left_by_an_interrupted_stop(folder):
    """The launcher's own STOP file, left when its stop was cut off, while no launcher runs: not a deliberate stop."""
    (folder / "STOP").write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    clock = Clock()
    spawn = Spawn(clock)
    seen = []
    clock.hooks.append((clock.now + 3, lambda: seen.append((folder / "STOP").exists())))
    stop_at(clock, folder, clock.now + 5)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.calls) == 3 and spawn.times("runner") == [1001]       # after the claim's tick
    assert seen == [False]
    assert log(folder).count("a STOP file left by an interrupted stop was removed") == 1


def test_a_stop_the_buyer_asks_in_the_moment_a_start_removes_a_leftover_stop_file_is_obeyed(folder, monkeypatch, capsys):
    """--stop puts its STOP file in place between the start's look at the launcher's leftover STOP file and its removal: the file is
    moved aside and read there, as at the end of a stop, so the buyer's STOP is seen, put back and obeyed."""
    stop_file = folder / "STOP"
    stop_file.write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    real_replace, real_unlink = os.replace, Path.unlink

    def the_buyers_stop():
        stop_file.write_text("", encoding="utf-8")

    def replace(source, target):
        if Path(source) == stop_file:                          # in this very moment
            the_buyers_stop()
        return real_replace(source, target)

    def unlink(self, *args, **kwargs):
        if self == stop_file:
            the_buyers_stop()
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(start.os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    clock = Clock()
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == [] and stop_file.read_text(encoding="utf-8") == ""
    assert "stopped by the STOP file" in capsys.readouterr().out and "interrupted stop" not in log(folder)
    assert sorted(path.name for path in folder.iterdir()) == ["STOP", "logs", "settings.json"]


# --- a runner that a launcher may have left behind ---

@pytest.mark.parametrize("poll, wait", [(30, 35), (2, 7), (None, 65)])
def test_a_start_gives_a_runner_left_behind_its_poll_time_and_5_seconds_to_end_by_the_stop_file(folder, capsys, poll, wait):
    """Where the parts are not tied to the launcher (off Windows, or when the tie failed), a launcher that is ended outright leaves its
    runner running, and that runner ends only at its next pass once STOP is there. A start that finds such a runner in a leftover
    state file makes the launcher's own STOP file, waits the recorded poll time plus 5 seconds (60 plus 5 when none is recorded),
    removes its STOP file and goes on: never two runners on one folder."""
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(11, tied=False), "review": part(12, command="aid.review", tied=False)},
                runner_poll=poll)
    stop_file = folder / "STOP"
    seen = []
    clock.hooks.append((clock.now + 1, lambda: seen.append((stop_file.read_text(encoding="utf-8"), len(spawn.calls)))))
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + wait + 10)
    assert run(folder, clock, spawn) == 0
    (text, started), = seen
    assert text.startswith("launcher ") and started == 0      # during the wait: the launcher's own STOP file, and no part started
    assert spawn.times("runner") == [1000 + wait + 1]         # after the wait, and the claim's tick
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"a runner that may be left behind (process 11) is given {wait} seconds to end by the STOP file"
    assert "interrupted stop" not in log(folder)


@pytest.mark.parametrize("poll, recorded", [(30, 30), (None, 60)])
def test_the_state_file_records_the_runners_poll_time_for_a_start_after_a_leftover(folder, poll, recorded):
    clock = Clock()
    seen = {}
    clock.hooks.append((clock.now + 3, lambda: seen.update(read_state(folder)["launcher"])))
    stop_at(clock, folder, clock.now + 5)
    assert run(folder, clock, Spawn(clock), poll=poll) == 0
    assert seen["runner_poll_seconds"] == recorded            # the runner's own default when none was given


def test_a_start_keeps_a_leftover_stop_file_of_the_launcher_for_the_wait_and_then_removes_it(folder):
    clock = Clock()
    stop_file = folder / "STOP"
    stop_file.write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    write_state(folder, clock.now - 300, parts={"runner": part(11)}, runner_poll=10)    # a record of before: not tied
    seen = []
    clock.hooks.append((clock.now + 1, lambda: seen.append(stop_file.read_text(encoding="utf-8"))))
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn) == 0
    assert seen == ["launcher 2026-11-10T08:43:09"] and spawn.times("runner") == [1016]
    text = log(folder)
    assert "is given 15 seconds to end" in text and text.count("a STOP file left by an interrupted stop was removed") == 1


def test_a_start_after_a_launcher_whose_parts_were_tied_does_not_wait(folder, capsys):
    """On Windows the job object ended the runner with the launcher: there is nothing to wait for."""
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(11, tied=True)}, runner_poll=30)
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 10)
    assert run(folder, clock, spawn) == 0
    assert spawn.times("runner") == [1001] and "left behind" not in capsys.readouterr().out


def test_a_stop_the_buyer_asks_during_the_wait_for_a_runner_left_behind_is_obeyed(folder, capsys):
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(11)}, runner_poll=30)
    clock.hooks.append((clock.now + 10, lambda: start._place_stop(folder, clock.sleep)))       # --stop, during the wait
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == [] and (folder / "STOP").read_text(encoding="utf-8") == ""
    assert "stopped by the STOP file" in capsys.readouterr().out


def test_a_start_after_a_fresh_leftover_with_a_runner_left_behind_watches_and_then_waits(folder):
    clock = Clock()
    write_state(folder, clock.now - 3, parts={"runner": part(11)}, runner_poll=2)
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn) == 0
    assert spawn.times("runner") == [1000 + 7 + 7 + 1]          # the watch, the wait of the poll time and 5 seconds, the claim


def test_the_launchers_own_stop_file_is_left_alone_while_that_launcher_runs(folder, capsys):
    """A launcher in its own stop: its heartbeat moves, so its STOP file is no leftover."""
    (folder / "STOP").write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    clock = Clock()
    beating(clock, folder, seconds=30)
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == []
    assert (folder / "STOP").read_text(encoding="utf-8") == "launcher 2026-11-10T08:43:09"
    assert "already running" in capsys.readouterr().out
    assert "interrupted stop" not in log(folder)


def test_a_stop_the_buyer_asks_while_a_start_watches_the_heartbeat_is_obeyed(folder, capsys):
    """A launcher's own STOP file and a fresh heartbeat that does not move; during the watch --stop writes the buyer's STOP."""
    (folder / "STOP").write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    clock = Clock()
    write_state(folder, clock.now - 5)
    clock.hooks.append((clock.now + 3, lambda: (folder / "STOP").write_text("", encoding="utf-8")))
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == [] and (folder / "STOP").read_text(encoding="utf-8") == ""
    assert "stopped by the STOP file" in capsys.readouterr().out and "interrupted stop" not in log(folder)


def test_a_launchers_stop_file_that_its_launcher_deleted_during_the_watch_is_not_reported_as_removed(folder):
    """A launcher at the end of its own stop: within the watch it deletes its STOP file and its state file, and the start goes on."""
    (folder / "STOP").write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    clock = Clock()
    write_state(folder, clock.now - 1)

    def launcher_ends():
        (folder / "STOP").unlink()
        (folder / start.STATE_FILE).unlink()
    clock.hooks.append((clock.now + 2, launcher_ends))
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 10)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.calls) == 3 and spawn.times("runner") == [1003]       # the end seen at 1002, then the claim's tick
    assert "interrupted stop" not in log(folder) and "left over" not in log(folder)


def test_stop_during_the_run_stops_the_page_and_the_server_and_waits_for_the_runner(folder):
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file, after=4)})
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn, poll=5) == 0
    review, capture, runner = spawn.procs["review"][0], spawn.procs["capture"][0], spawn.procs["runner"][0]
    assert review.terminated == 1 and capture.terminated == 1 and not review.killed
    assert runner.terminated == 0 and runner.killed == 0 and runner.returncode == 0
    assert not (folder / start.STATE_FILE).exists()
    assert stop_file.read_text(encoding="utf-8") == ""         # the buyer's switch stays, unchanged, until --resume
    text = log(folder)
    assert text.rstrip().endswith("stopped")
    assert [text.count(f"{name} ended with exit code") for name in ("runner", "review", "capture")] == [1, 1, 1]


def test_a_part_that_ignores_terminate_is_killed_ten_seconds_later(folder):
    clock = Clock()
    spawn = Spawn(clock, {"runner": [(35, 0)]}, ignores_terminate=("review",))
    stop_at(clock, folder, clock.now + 30)
    assert run(folder, clock, spawn, poll=60) == 0
    review = spawn.procs["review"][0]
    assert review.terminated == 1 and review.killed == 1
    assert 10 <= review.killed_at - review.terminated_at <= 11
    assert spawn.procs["capture"][0].killed == 0


def test_a_runner_that_does_not_end_is_ended_after_its_poll_time_and_thirty_seconds(folder):
    clock = Clock()
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 10)
    assert run(folder, clock, spawn, poll=20) == 0
    runner = spawn.procs["runner"][0]
    assert runner.terminated == 1
    assert 50 <= runner.terminated_at - 1010 <= 52


def test_without_a_poll_time_the_runner_gets_its_default_poll_time_and_thirty_seconds(folder):
    clock = Clock()
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 10)
    assert run(folder, clock, spawn) == 0
    runner = spawn.procs["runner"][0]
    assert runner.terminated == 1
    assert 60 + 30 <= runner.terminated_at - 1010 <= 60 + 30 + 2


def test_a_part_that_waits_to_be_started_again_is_not_started_once_the_stop_has_begun(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(2, 1)]})                # it ends after 2 seconds and is due again 5 seconds later
    stop_at(clock, folder, clock.now + 4)
    seen = {}
    clock.hooks.append((clock.now + 12, lambda: seen.update(read_state(folder)["parts"])))
    assert run(folder, clock, spawn, poll=20) == 0
    assert len(spawn.times("capture")) == 1
    assert seen["capture"]["state"] == "stopped" and seen["capture"]["pid"] is None


def test_the_heartbeat_is_kept_while_the_launcher_stops(folder):
    clock = Clock()
    spawn = Spawn(clock)                                       # a runner that does not end by itself: a stop of 50 seconds
    stop_at(clock, folder, clock.now + 10)
    seen = {}
    clock.hooks.append((clock.now + 45, lambda: seen.update(read_state(folder)["launcher"])))
    assert run(folder, clock, spawn, poll=20) == 0
    assert seen["heartbeat"] >= 1040


def test_the_heartbeat_is_never_older_than_heartbeat_every_through_a_run_and_a_stop(folder):
    """Read every second, as a start that watches it does: the watch of 7 seconds relies on a heartbeat written at least every 5."""
    clock = Clock()
    spawn = Spawn(clock)                                       # a runner that does not end by itself: a stop of 50 seconds
    stop_at(clock, folder, clock.now + 20)
    ages = []

    def look():
        if (folder / start.STATE_FILE).exists():
            ages.append(clock.now - read_state(folder)["launcher"]["heartbeat"])
    for second in range(1, 90):
        clock.hooks.append((clock.now + second, look))
    assert run(folder, clock, spawn, poll=20) == 0
    assert len(ages) >= 65 and max(ages) <= start.HEARTBEAT_EVERY


# --- a stop the launcher starts itself: Ctrl-C and the signals ---

def test_ctrl_c_stops_as_stop_does_with_a_stop_file_of_its_own_for_the_time_of_the_stop(folder):
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    assert run(folder, clock, spawn, poll=5, sleep=interrupted_at(clock, 5)) == 0
    runner = spawn.procs["runner"][0]
    assert runner.returncode == 0 and runner.terminated == 0 and runner.killed == 0     # it ended by itself, at its next pass
    assert re.fullmatch(r"launcher \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", runner.stop_text)
    assert spawn.procs["review"][0].terminated == 1 and spawn.procs["capture"][0].terminated == 1
    assert not stop_file.exists()                              # gone again when the parts have ended
    assert not (folder / start.STATE_FILE).exists()
    text = log(folder)
    assert "Ctrl-C: stopping the parts" in text and text.rstrip().endswith("stopped")
    assert "runner ended with exit code 0" in text


def test_a_stop_file_that_was_already_there_is_not_changed_or_deleted_by_the_launchers_own_stop(folder):
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    count = {"n": 0}

    def sleep(seconds):                                         # the buyer's STOP appears in the second of the Ctrl-C
        count["n"] += 1
        if count["n"] == 5:
            stop_file.write_text("the buyer's", encoding="utf-8")
            raise KeyboardInterrupt
        clock.sleep(seconds)
    assert run(folder, clock, spawn, poll=5, sleep=sleep) == 0
    assert stop_file.read_text(encoding="utf-8") == "the buyer's"
    assert spawn.procs["runner"][0].stop_text == "the buyer's"


def test_a_stop_the_buyer_asks_during_the_launchers_own_stop_stays(folder):
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file, after=10)})
    clock.hooks.append((clock.now + 8, lambda: stop_file.write_text("", encoding="utf-8")))     # as --stop writes it
    assert run(folder, clock, spawn, poll=20, sleep=interrupted_at(clock, 5)) == 0
    assert spawn.procs["runner"][0].stop_text.startswith("launcher ")
    assert stop_file.read_text(encoding="utf-8") == ""
    assert sorted(path.name for path in folder.iterdir()) == ["STOP", "logs", "settings.json"]


def test_a_stop_the_buyer_asks_in_the_moment_the_launcher_deletes_its_own_stop_file_stays(folder, monkeypatch):
    """--stop writes STOP between the launcher's last look at the file and its removal: the file is moved aside before it is
    read, so the buyer's STOP is seen there and put back."""
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    real = os.replace

    def replace(source, target):
        if Path(source) == stop_file:                          # in this very moment
            stop_file.write_text("", encoding="utf-8")
        return real(source, target)
    monkeypatch.setattr(start.os, "replace", replace)
    assert run(folder, clock, spawn, poll=5, sleep=interrupted_at(clock, 5)) == 0
    assert spawn.procs["runner"][0].stop_text.startswith("launcher ")
    assert stop_file.read_text(encoding="utf-8") == ""
    assert sorted(path.name for path in folder.iterdir()) == ["STOP", "logs", "settings.json"]


@pytest.mark.parametrize("number", STOP_SIGNALS, ids=lambda number: number.name)
def test_a_stop_signal_starts_the_same_stop_as_ctrl_c(folder, number):
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    before = handlers()
    assert run(folder, clock, spawn, poll=5, sleep=signalled_at(clock, {4: number})) == 0
    runner = spawn.procs["runner"][0]
    assert runner.returncode == 0 and runner.terminated == 0 and runner.killed == 0
    assert runner.stop_text.startswith("launcher ")
    assert spawn.procs["review"][0].terminated == 1 and spawn.procs["capture"][0].terminated == 1
    assert not stop_file.exists() and not (folder / start.STATE_FILE).exists()
    text = log(folder)
    assert f"{number.name}: stopping the parts" in text and text.rstrip().endswith("stopped")
    assert handlers() == before                                # the handlers that were there before are back


def test_a_real_ctrl_c_is_the_same_stop(folder):
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    before = handlers()
    assert run(folder, clock, spawn, poll=5, sleep=signalled_at(clock, {4: signal.SIGINT})) == 0
    runner = spawn.procs["runner"][0]
    assert runner.returncode == 0 and runner.terminated == 0 and runner.stop_text.startswith("launcher ")
    assert "Ctrl-C: stopping the parts" in log(folder)
    assert not stop_file.exists() and handlers() == before


def test_a_second_signal_during_the_stop_acts_as_a_second_ctrl_c(folder):
    clock = Clock()
    spawn = Spawn(clock)                                       # a runner that does not end by itself
    assert run(folder, clock, spawn, poll=20, sleep=signalled_at(clock, {4: signal.SIGTERM, 8: signal.SIGTERM})) == 0
    runner = spawn.procs["runner"][0]
    assert runner.killed == 1 and runner.killed_at == 1008     # at once, not after the runner's 50 seconds
    assert not (folder / "STOP").exists() and not (folder / start.STATE_FILE).exists()
    text = log(folder)
    assert "SIGTERM: stopping the parts" in text and "SIGTERM again: ending the parts at once" in text
    assert text.rstrip().endswith("stopped")


def test_signals_that_come_together_are_one_request(folder):
    """A terminal that is closed and a system that shuts down send more than one signal at a time: the runner still ends by itself."""
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    assert run(folder, clock, spawn, poll=5, sleep=signalled_at(clock, {4: (STOP_SIGNALS[0], STOP_SIGNALS[-1], signal.SIGINT)})) == 0
    runner = spawn.procs["runner"][0]
    assert runner.returncode == 0 and runner.terminated == 0 and runner.killed == 0
    text = log(folder)
    assert f"{STOP_SIGNALS[0].name}: stopping the parts" in text and "again" not in text and "at once" not in text
    assert not stop_file.exists() and not (folder / start.STATE_FILE).exists()


def test_a_signal_within_a_second_of_the_one_that_asked_belongs_to_the_same_request(folder):
    """As when the shell passes on the hangup that the launcher already got from the terminal; a later one is a second request."""
    clock = Clock()
    number = STOP_SIGNALS[-1]

    def review():
        proc = Proc(clock)
        proc.terminate = lambda: (send(number), Proc.terminate(proc))[1]      # it comes while the stop ends the review page
        return proc
    spawn = Spawn(clock, makers={"review": review})
    assert run(folder, clock, spawn, poll=20, sleep=signalled_at(clock, {4: number, 9: number})) == 0
    runner = spawn.procs["runner"][0]
    assert runner.killed == 1 and runner.killed_at == 1009     # not at 1004, when the stop began, but at the signal of 1009
    text = log(folder)
    assert text.count("again: ending the parts at once") == 1


def test_a_second_signal_counts_from_the_one_that_asked_not_from_the_look_that_acted_on_it(folder):
    """The first signal comes at the start of the launcher's sleep and is acted on a second later; one that comes then, a second
    after the first, is a second request."""
    clock = Clock()
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        if count["n"] == 4:
            send(signal.SIGTERM)
        clock.sleep(seconds)

    def review():
        proc = Proc(clock)
        proc.terminate = lambda: (send(signal.SIGTERM), Proc.terminate(proc))[1]
        return proc
    spawn = Spawn(clock, makers={"review": review})
    assert run(folder, clock, spawn, poll=20, sleep=sleep) == 0
    runner = spawn.procs["runner"][0]
    assert runner.killed == 1 and runner.killed_at == 1004
    assert "SIGTERM again: ending the parts at once" in log(folder)


def test_a_second_ctrl_c_ends_the_parts_at_once(folder):
    clock = Clock()
    spawn = Spawn(clock)
    assert run(folder, clock, spawn, poll=20, sleep=interrupted_at(clock, 5, 9)) == 0
    runner = spawn.procs["runner"][0]
    assert runner.killed == 1 and runner.returncode == -9
    assert not (folder / "STOP").exists() and not (folder / start.STATE_FILE).exists()
    text = log(folder)
    assert "Ctrl-C again: ending the parts at once" in text and text.rstrip().endswith("stopped")


def test_a_second_and_a_third_ctrl_c_cannot_skip_the_end_of_the_stop(folder):
    """The third Ctrl-C comes while the parts are being killed: Ctrl-C and the stop signals are ignored then."""
    clock = Clock()
    seen = {}

    def third_ctrl_c():
        seen.update(handlers())
        signal.raise_signal(signal.SIGINT)

    def runner():
        proc = Proc(clock, dies_after_kill=2)
        proc.on_kill = third_ctrl_c
        return proc
    spawn = Spawn(clock, makers={"runner": runner})
    before = handlers()
    assert run(folder, clock, spawn, poll=20, sleep=signalled_at(clock, {4: signal.SIGINT, 7: signal.SIGINT})) == 0
    assert seen and all(handler is signal.SIG_IGN for handler in seen.values())
    assert handlers() == before                                # and the earlier handlers are back afterwards
    proc = spawn.procs["runner"][0]
    assert proc.killed == 1 and proc.returncode == -9
    assert not (folder / "STOP").exists() and not (folder / start.STATE_FILE).exists()
    text = log(folder)
    assert "Ctrl-C: stopping the parts" in text and "Ctrl-C again: ending the parts at once" in text
    assert text.rstrip().endswith("stopped")


def test_an_interrupt_while_the_parts_are_killed_does_not_skip_the_end_either(folder):
    """A sleep that raises KeyboardInterrupt a third time, in the wait for the killed parts."""
    clock = Clock()
    spawn = Spawn(clock, makers={"runner": lambda: Proc(clock, dies_after_kill=3)})
    assert run(folder, clock, spawn, poll=20, sleep=interrupted_at(clock, 5, 8, 9)) == 0
    assert spawn.procs["runner"][0].killed == 1
    assert not (folder / "STOP").exists() and not (folder / start.STATE_FILE).exists()
    assert log(folder).rstrip().endswith("stopped")


def test_a_ctrl_c_during_a_stop_by_the_stop_file_ends_the_parts_at_once_and_leaves_the_file(folder):
    clock = Clock()
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 3)
    assert run(folder, clock, spawn, poll=20, sleep=interrupted_at(clock, 8)) == 0
    assert spawn.procs["runner"][0].killed == 1
    assert (folder / "STOP").read_text(encoding="utf-8") == "" and not (folder / start.STATE_FILE).exists()
    text = log(folder)
    assert "Ctrl-C: ending the parts at once" in text and text.rstrip().endswith("stopped")


def test_a_signal_that_was_ignored_before_the_launcher_stays_ignored(folder):
    """As under nohup: the launcher does not take a signal that its caller had ignored."""
    number = STOP_SIGNALS[-1]
    earlier = signal.signal(number, signal.SIG_IGN)
    try:
        clock = Clock()
        spawn = Spawn(clock)
        stop_at(clock, folder, clock.now + 10)
        assert run(folder, clock, spawn, poll=1, sleep=signalled_at(clock, {4: number})) == 0
        assert f"{number.name}: stopping" not in log(folder) and "the STOP file appeared" in log(folder)
        assert signal.getsignal(number) is signal.SIG_IGN
    finally:
        signal.signal(number, earlier)


def test_outside_the_main_thread_the_launcher_sets_no_handler(folder):
    clock = Clock()
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 3)
    before, result = handlers(), []

    def in_thread():
        try:
            result.append(run(folder, clock, spawn))
        except BaseException as exc:                           # a failure is kept for the assertion below
            result.append(exc)
    thread = threading.Thread(target=in_thread)
    thread.start()
    thread.join(60)
    assert result == [0] and handlers() == before
    assert log(folder).rstrip().endswith("stopped")


def test_sigquit_starts_the_stop_as_the_other_signals_do_where_the_system_has_it():
    """Ctrl-\\ in a terminal of Linux or macOS: the parts have a session of their own, so it reaches the launcher alone."""
    assert set(STOP_SIGNALS) <= set(start.STOP_SIGNALS)
    if hasattr(signal, "SIGQUIT"):
        assert signal.SIGQUIT in start.STOP_SIGNALS


def test_after_a_stop_the_command_keeps_ctrl_c_and_the_stop_signals_ignored_until_the_process_ends(folder, monkeypatch):
    """A Ctrl-C that comes after "stopped" (a key held down) would end the process by the signal: exit code -2, not 0. In the
    command the signals stay ignored; supervise() used as a function puts the handlers back (the tests above)."""
    example_settings(folder)
    clock = Clock()
    stop_at(clock, folder, clock.now + 3)
    with_fakes(monkeypatch, "supervise", spawn=Spawn(clock), clock=clock, sleep=clock.sleep, wall=clock, tie=no_tie)
    assert start.main(["--settings", str(folder / "settings.json")]) == 0
    assert log(folder).rstrip().endswith("stopped")
    assert all(handler is signal.SIG_IGN for handler in handlers().values())
    for number in handlers():                                 # late signals change nothing
        signal.raise_signal(number)


# --- a fault of the launcher ---

def test_a_fault_of_the_launcher_ends_the_parts_removes_the_state_file_and_leaves_its_traceback(folder):
    clock = Clock()
    spawn = Spawn(clock, ignores_terminate=("review",))
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        if count["n"] == 5:
            raise RuntimeError("a fault of the launcher itself")
        clock.sleep(seconds)
    before = handlers()
    with pytest.raises(RuntimeError, match="a fault of the launcher itself"):
        run(folder, clock, spawn, sleep=sleep)
    for name in ("runner", "review", "capture"):
        assert spawn.procs[name][0].returncode is not None     # no part is left running without a launcher
    review = spawn.procs["review"][0]
    assert review.terminated == 1 and review.killed == 1 and 10 <= review.killed_at - review.terminated_at <= 11
    assert not (folder / start.STATE_FILE).exists() and not (folder / "STOP").exists()
    assert handlers() == before
    text = log(folder)
    assert "the launcher failed (RuntimeError): stopping the parts" in text
    assert "Traceback (most recent call last)" in text and "RuntimeError: a fault of the launcher itself" in text
    assert text.count("Traceback (most recent call last)") == 1


def test_a_fault_that_repeats_during_the_cleanup_still_ends_the_parts(folder):
    clock = Clock()
    spawn = Spawn(clock, ignores_terminate=("review",))

    def sleep(seconds):
        if clock.slept >= 4:
            raise RuntimeError("the sleep itself is broken")
        clock.sleep(seconds)
    with pytest.raises(RuntimeError):
        run(folder, clock, spawn, sleep=sleep)
    assert all(proc.returncode is not None for proc in spawn.order)
    assert spawn.procs["review"][0].killed == 1
    assert not (folder / start.STATE_FILE).exists()


def test_an_exit_raised_inside_the_supervision_leaves_no_part_running_either(folder):
    clock = Clock()
    spawn = Spawn(clock)
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        if count["n"] == 4:
            raise SystemExit(7)
        clock.sleep(seconds)
    with pytest.raises(SystemExit):
        run(folder, clock, spawn, sleep=sleep)
    assert all(proc.returncode is not None for proc in spawn.order) and len(spawn.order) == 3
    assert not (folder / start.STATE_FILE).exists()


def supervisor(folder: Path, clock: Clock, spawn, sleep, poll: float = 20):
    """A supervisor whose parts run, as inside supervise() after the checks of a start."""
    made = start._Supervisor((folder / "settings.json").resolve(), parts(folder, poll), spawn, clock, sleep, clock, poll,
                             start._Log(folder, clock), no_tie)
    for one in made.parts:
        made.start(one)
    made.write_state()
    return made


def test_whatever_interrupts_a_stop_its_end_is_made(folder):
    """Not a Ctrl-C and not a fault: an exit raised in the wait for the runner. The end of the stop is made on the way out."""
    clock = Clock()
    spawn = Spawn(clock)
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        if count["n"] == 3:
            raise SystemExit(7)
        clock.sleep(seconds)
    made = supervisor(folder, clock, spawn, sleep)
    with pytest.raises(SystemExit):
        made.stop(KeyboardInterrupt())
    assert spawn.procs["runner"][0].killed == 1 and all(proc.returncode is not None for proc in spawn.order)
    assert not (folder / start.STATE_FILE).exists() and not (folder / "STOP").exists()
    assert log(folder).rstrip().endswith("stopped")


def test_the_end_of_a_stop_puts_back_the_handlers_it_found(folder):
    clock = Clock()
    made = supervisor(folder, clock, Spawn(clock), clock.sleep)

    def found(number, frame):
        """The handler in place when the end of the stop begins."""
    during = {}
    made.parts[0].proc.on_kill = lambda: during.update(handlers())
    earlier = {number: signal.signal(number, found) for number in (signal.SIGINT, *STOP_SIGNALS)}
    try:
        made.finish()
        assert during and all(handler is signal.SIG_IGN for handler in during.values())
        assert all(handler is found for handler in handlers().values())
    finally:
        for number, handler in earlier.items():
            signal.signal(number, handler)
    assert not (folder / start.STATE_FILE).exists() and made.parts[0].proc is None


def test_a_fault_inside_the_end_of_a_stop_still_removes_both_files_and_writes_stopped(folder):
    """A part that takes seconds to die after its kill, and a sleep that fails while the end of the stop waits for it."""
    clock = Clock()

    def broken(seconds):
        raise RuntimeError("the sleep itself is broken")
    spawn = Spawn(clock, makers={"runner": lambda: Proc(clock, dies_after_kill=3)})
    made = supervisor(folder, clock, spawn, broken)
    made.make_stop()
    assert (folder / "STOP").read_text(encoding="utf-8").startswith("launcher ")
    with pytest.raises(RuntimeError):
        made.finish()
    assert spawn.procs["runner"][0].killed == 1
    assert not (folder / start.STATE_FILE).exists() and not (folder / "STOP").exists()
    assert log(folder).rstrip().endswith("stopped")


def test_a_part_that_survives_its_kill_is_named_with_its_process_id_to_close_by_hand(folder):
    clock = Clock()
    spawn = Spawn(clock, makers={"runner": lambda: Proc(clock, dies_after_kill=float("inf"))})
    made = supervisor(folder, clock, spawn, clock.sleep)
    made.finish()
    runner = spawn.procs["runner"][0]
    assert f"runner (process {runner.pid}) has not ended: close it by hand" in log(folder)


def test_a_fault_outside_the_supervision_leaves_its_traceback_in_start_log_and_ends_with_1(folder, monkeypatch, capsys):
    """Under pythonw there is no window to show it."""
    example_settings(folder)

    def broken(*args, **kwargs):
        raise RuntimeError("a fault outside the supervision")
    monkeypatch.setattr(start, "supervise", broken)
    assert start.main(["--settings", str(folder / "settings.json")]) == 1
    text = log(folder)
    assert "the launcher failed (RuntimeError)" in text
    assert "Traceback (most recent call last)" in text and "RuntimeError: a fault outside the supervision" in text


def test_a_fault_of_the_supervision_is_logged_once_and_the_command_ends_with_1(folder, monkeypatch):
    example_settings(folder)
    clock = Clock()
    spawn = Spawn(clock)
    count = {"n": 0}

    def sleep(seconds):
        count["n"] += 1
        if count["n"] == 3:
            raise RuntimeError("a fault of the launcher itself")
        clock.sleep(seconds)
    real = start.supervise
    monkeypatch.setattr(start, "supervise", lambda settings, commands, **kwargs: real(
        settings, commands, spawn=spawn, clock=clock, sleep=sleep, wall=clock, tie=no_tie))
    assert start.main(["--settings", str(folder / "settings.json")]) == 1
    text = log(folder)
    assert text.count("Traceback (most recent call last)") == 1 and text.count("RuntimeError: a fault of the launcher itself") == 1
    assert all(proc.returncode is not None for proc in spawn.order)


# --- --stop, --resume and --background ---

def test_stop_option_creates_stop_says_at_once_that_it_waits_and_waits_for_the_launcher_to_end(folder, capsys):
    clock = Clock()
    state = folder / start.STATE_FILE
    write_state(folder, clock.now)
    said = []
    clock.hooks.append((clock.now + 1, lambda: said.append(capsys.readouterr().out)))
    clock.hooks.append((clock.now + 7, state.unlink))
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    assert (folder / "STOP").read_text(encoding="utf-8") == ""
    assert "waiting for the parts to end (up to 90 seconds)" in said[0]      # before the wait, not after it
    assert "ended" in capsys.readouterr().out
    assert clock.now - 1000 < 10


@pytest.mark.parametrize("age, waits", [(19, True), (21, False)])
def test_stop_option_takes_a_heartbeat_younger_than_twenty_seconds_for_a_running_launcher(folder, capsys, age, waits):
    clock = Clock()
    write_state(folder, clock.now - age)
    clock.hooks.append((clock.now + 3, (folder / start.STATE_FILE).unlink))
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    assert (clock.slept > 0) is waits
    assert ("no launcher runs" in capsys.readouterr().out) is not waits


def test_stop_option_without_a_launcher_creates_stop_and_does_not_wait(folder, capsys):
    clock = Clock()
    opener = answering()
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock, opener=opener) == 0
    assert (folder / "STOP").exists()
    assert clock.slept == 0
    out = capsys.readouterr().out
    assert "no launcher" in out and "by hand" not in out
    assert opener.asked == [(REVIEW, 2), (CAPTURE, 2)]         # the two addresses, once


def test_stop_option_without_a_launcher_names_the_parts_that_still_answer(folder, capsys):
    """STOP ends a runner only. What answers is a part started by hand, a part a launcher left behind, or a launcher on other
    settings (only one runs on a PC); a process id is given only for a part that was not tied to its launcher."""
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(11), "review": part(12, command="aid.review", tied=False),
                                                "capture": part(13, command="aid.capture_server", tied=True)})
    opener = answering(7860, 7870)
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock, opener=opener) == 0
    assert opener.asked == [(REVIEW, 2), (CAPTURE, 2)]
    lines = capsys.readouterr().out.splitlines()
    review, = [line for line in lines if REVIEW in line]
    capture, = [line for line in lines if CAPTURE in line]
    assert review == (f"{REVIEW} (review) still answers, and STOP does not end it: {ONE_LAUNCHER}; the state file (start_state.json) "
                      "of the last launcher names it as running (process 12)")
    assert capture == f"{CAPTURE} (capture) still answers, and STOP does not end it: {ONE_LAUNCHER}"
    assert clock.slept == 0


def test_stop_option_puts_its_stop_file_in_place_with_a_replace(folder, monkeypatch):
    """Written beside STOP and moved over it: a STOP file that a launcher is making or removing in the same moment is replaced,
    never emptied in place (where the launcher's text could land after it, and the file would count as the launcher's own)."""
    stop_file = folder / "STOP"
    stop_file.write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    before = os.stat(stop_file).st_ino
    real, moves = os.replace, []

    def replace(source, target):
        moves.append((Path(source), Path(target)))
        return real(source, target)
    monkeypatch.setattr(start.os, "replace", replace)
    clock = Clock()
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    (source, target), = moves
    assert target == stop_file and source.parent == folder and source.name != "STOP"
    assert stop_file.read_text(encoding="utf-8") == "" and os.stat(stop_file).st_ino != before
    assert sorted(path.name for path in folder.iterdir()) == ["STOP", "logs", "settings.json"]


def test_stop_option_tries_once_more_when_the_stop_file_is_held_open(folder, monkeypatch):
    clock = Clock()
    real, tried = os.replace, []

    def replace(source, target):
        tried.append(clock.now)
        if len(tried) == 1:
            raise PermissionError(13, "the file is in use")    # on Windows while another program reads it
        return real(source, target)
    monkeypatch.setattr(start.os, "replace", replace)
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    assert len(tried) == 2 and tried[1] - tried[0] == pytest.approx(start.RETRY)
    assert (folder / "STOP").read_text(encoding="utf-8") == ""


def test_a_stop_the_buyer_asks_while_the_launcher_makes_its_own_stop_file_stays(folder, monkeypatch):
    """--stop in the moment between the creation of the launcher's own STOP file and its text: the text lands in the file that
    --stop replaced, so the STOP file in place is the buyer's, and the end of the stop leaves it."""
    clock = Clock()
    stop_file = folder / "STOP"
    spawn = Spawn(clock, makers={"runner": lambda: ObedientRunner(clock, stop_file)})
    real_open = open

    class Late:
        """The launcher's own STOP file, whose text is written only after the buyer's --stop."""

        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)

        def write(self, text):
            start._place_stop(folder, clock.sleep)
            return self.handle.write(text)

    def late_open(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return Late(handle) if Path(path) == stop_file and "x" in mode else handle
    monkeypatch.setattr(start, "open", late_open, raising=False)
    assert run(folder, clock, spawn, poll=5, sleep=interrupted_at(clock, 5)) == 0
    assert stop_file.read_text(encoding="utf-8") == ""
    assert sorted(path.name for path in folder.iterdir()) == ["STOP", "logs", "settings.json"]


def test_stop_option_says_when_the_parts_have_not_ended_in_90_seconds(folder, capsys):
    clock = Clock()
    beating(clock, folder, seconds=200)
    start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock)
    assert 90 <= clock.now - 1000 <= 92
    assert "not ended" in capsys.readouterr().out


def test_stop_option_leaves_a_stop_file_that_resume_deleted_meanwhile(folder, capsys):
    """--stop, then --resume while it waits: the later command decides, so --stop does not create STOP a second time."""
    clock = Clock()
    write_state(folder, clock.now)

    def resume_after_the_launcher_ended():
        (folder / start.STATE_FILE).unlink()
        (folder / "STOP").unlink()
    clock.hooks.append((clock.now + 4, resume_after_the_launcher_ended))
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    assert not (folder / "STOP").exists()
    assert "the parts have ended" in capsys.readouterr().out


def test_stop_option_sees_that_another_launcher_was_started_meanwhile(folder, capsys):
    """--resume during the stop started a launcher again before --stop looked: its state file is not the one --stop waits for."""
    clock = Clock()
    beating(clock, folder, seconds=4)

    def another_launcher():
        (folder / start.STATE_FILE).write_text(json.dumps({
            "launcher": {"pid": 8, "started": "2026-11-10T09:00:00", "heartbeat": clock.now, "heartbeat_at": "x"}, "parts": {}}),
            encoding="utf-8")
    for second in range(5, 200):
        clock.hooks.append((clock.now + second, another_launcher))
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    assert 5 <= clock.now - 1000 <= 6                          # not the whole 90 seconds
    out = capsys.readouterr().out
    assert "the parts have ended" in out and "another launcher" in out and "not ended" not in out


def test_stop_option_says_when_the_launcher_stops_writing_its_heartbeat(folder, capsys):
    """The launcher was ended from outside while --stop waited: its state file stays, its heartbeat grows old."""
    clock = Clock()
    write_state(folder, clock.now)
    assert start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 0
    assert 20 <= clock.now - 1000 <= 21
    assert "without ending cleanly" in capsys.readouterr().out


def test_stop_option_does_not_take_a_state_file_it_cannot_read_for_a_launcher_that_ended(folder, capsys):
    clock = Clock()
    beating(clock, folder, seconds=200)
    state = folder / start.STATE_FILE
    clock.hooks.append((clock.now + 3, lambda: state.write_text("{ half a file", encoding="utf-8")))
    start.stop(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock)
    assert 90 <= clock.now - 1000 <= 92
    assert "without ending cleanly" not in capsys.readouterr().out


@pytest.mark.parametrize("option", ["--stop", "--status"])
def test_stop_and_status_refuse_a_settings_file_that_does_not_exist_and_write_nothing(tmp_path, capsys, option):
    empty = tmp_path / "empty"
    empty.mkdir()
    for settings in (empty / "settings.json", tmp_path / "nofolder" / "settings.json"):
        assert start.main(["--settings", str(settings), option]) == 2
        out = capsys.readouterr()
        assert len(out.out.strip().splitlines()) == 1 and "does not exist" in out.out and out.err == ""
    assert list(empty.iterdir()) == [] and not (tmp_path / "nofolder").exists()


def test_stop_and_status_work_on_a_settings_file_that_exists_whatever_it_holds(folder, capsys):
    (folder / "settings.json").write_text("not json", encoding="utf-8")
    assert start.main(["--settings", str(folder / "settings.json"), "--status"]) == 1
    assert start.main(["--settings", str(folder / "settings.json"), "--stop"]) == 0
    assert (folder / "STOP").exists()


def test_resume_deletes_stop_and_starts(folder, monkeypatch):
    example_settings(folder)
    (folder / "STOP").write_text("", encoding="utf-8")
    seen = {}

    def supervise(settings_file, parts, **kwargs):
        seen["stop"] = (folder / "STOP").exists()
        seen["parts"], seen["kwargs"] = parts, kwargs
        return 0
    monkeypatch.setattr(start, "supervise", supervise)
    assert start.main(["--settings", str(folder / "settings.json"), "--resume", "--testshop"]) == 0
    assert seen["stop"] is False
    assert seen["parts"]["capture"][-1] == "--testshop"
    assert seen["parts"]["runner"][0] == sys.executable
    assert seen["kwargs"]["window"] is True
    assert "--resume: the STOP file is deleted" in log(folder)


def test_resume_waits_for_a_launcher_that_is_stopping_before_it_deletes_stop(folder, capsys):
    clock = Clock()
    (folder / "STOP").write_text("", encoding="utf-8")
    beating(clock, folder, seconds=40)                         # STOP exists and the heartbeat moves: that launcher is stopping
    seen = []
    clock.hooks.append((clock.now + 41, lambda: seen.append(((folder / "STOP").exists(), capsys.readouterr().out))))
    clock.hooks.append((clock.now + 42, (folder / start.STATE_FILE).unlink))
    assert start.resume(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) is None
    (stop_was_there, said), = seen
    assert stop_was_there                                      # left alone while that launcher was stopping
    assert len(said.strip().splitlines()) == 1 and "stopping" in said and "150 seconds" in said
    assert not (folder / "STOP").exists()
    assert 42 <= clock.now - 1000 <= 43                        # as soon as that launcher's state file was gone
    assert "--resume: the STOP file is deleted" in log(folder)


def test_resume_compares_the_heartbeat_with_the_wall_clock_by_default(folder, capsys):
    """The heartbeat is the wall clock's time, which another process can compare with its own; the monotonic clock of one process
    means nothing to another. A launcher that is stopping, by the real clock:"""
    (folder / "STOP").write_text("", encoding="utf-8")
    write_state(folder, time.time())
    clock = Clock()
    clock.hooks.append((clock.now + 2, (folder / start.STATE_FILE).unlink))
    assert start.resume(folder / "settings.json", sleep=clock.sleep, clock=clock) is None
    assert "still stopping" in capsys.readouterr().out and clock.now - 1000 == 2


def test_resume_goes_on_when_the_heartbeat_of_the_stopping_launcher_stops_moving(folder):
    clock = Clock()
    (folder / "STOP").write_text("", encoding="utf-8")
    beating(clock, folder, seconds=10)                         # its last heartbeat after 10 seconds: it was ended from outside
    assert start.resume(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) is None
    assert 17 <= clock.now - 1000 <= 18                        # HEARTBEAT_EVERY + 2 seconds without a new heartbeat
    assert not (folder / "STOP").exists()


def test_resume_gives_up_after_150_seconds_and_leaves_stop_untouched(folder, capsys):
    clock = Clock()
    (folder / "STOP").write_text("the buyer's", encoding="utf-8")
    beating(clock, folder, seconds=400)
    assert start.resume(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 3
    assert 150 <= clock.now - 1000 <= 151
    assert (folder / "STOP").read_text(encoding="utf-8") == "the buyer's"
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and "has not ended" in lines[1]


@pytest.mark.parametrize("stop, heartbeat", [(True, None), (True, 60), (False, 0)])
def test_resume_does_not_wait_when_no_launcher_is_stopping(folder, stop, heartbeat):
    """No state file, a stale one, or a launcher that runs without a STOP file (the start itself then refuses)."""
    clock = Clock()
    if stop:
        (folder / "STOP").write_text("", encoding="utf-8")
    if heartbeat is not None:
        write_state(folder, clock.now - heartbeat)
    assert start.resume(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) is None
    assert clock.slept == 0 and not (folder / "STOP").exists()


def test_resume_that_gives_up_starts_nothing(folder, monkeypatch):
    example_settings(folder)
    monkeypatch.setattr(start, "resume", lambda settings: 3)
    monkeypatch.setattr(start, "supervise", lambda *args, **kwargs: pytest.fail("started"))
    monkeypatch.setattr(start, "background", lambda *args, **kwargs: pytest.fail("started"))
    assert start.main(["--settings", str(folder / "settings.json"), "--resume"]) == 3
    assert start.main(["--settings", str(folder / "settings.json"), "--resume", "--background"]) == 3


def test_resume_waits_for_the_launcher_it_saw_and_starts_nothing_when_another_one_was_started_meanwhile(folder, capsys):
    """A second --resume during one stop: the first one deleted STOP and started a launcher, whose state file this one finds where
    the stopping launcher's was. That counts as the end of the stopping launcher, and this --resume starts nothing."""
    clock = Clock()
    (folder / "STOP").write_text("", encoding="utf-8")
    beating(clock, folder, seconds=4)                          # the launcher that is stopping (process 7)

    def another_launcher():
        (folder / start.STATE_FILE).write_text(json.dumps({
            "launcher": {"pid": 8, "started": "2026-11-10T09:00:00", "heartbeat": clock.now, "heartbeat_at": "x"}, "parts": {}}),
            encoding="utf-8")
    for second in range(5, 200):
        clock.hooks.append((clock.now + second, another_launcher))
    assert start.resume(folder / "settings.json", sleep=clock.sleep, wall=clock, clock=clock) == 3
    assert 5 <= clock.now - 1000 <= 6                          # at once, not after 150 seconds
    assert (folder / "STOP").exists()                          # left as it is
    out = capsys.readouterr().out
    assert "--resume: another launcher was started on these settings meanwhile; nothing started" in out and "150" in out


def test_resume_background_deletes_stop_and_starts_the_launcher_in_the_background_not_in_the_window(folder, monkeypatch):
    example_settings(folder)
    (folder / "STOP").write_text("", encoding="utf-8")
    seen = []
    monkeypatch.setattr(start, "supervise", lambda *args, **kwargs: pytest.fail("started in the window"))
    monkeypatch.setattr(start, "background", lambda *args, **kwargs: seen.append(((folder / "STOP").exists(), args)) or 0)
    assert start.main(["--settings", str(folder / "settings.json"), "--resume", "--testshop", "--background"]) == 0
    assert seen == [(False, ((folder / "settings.json").resolve(), True, None, sys.executable))]


@pytest.mark.parametrize("options", [[], ["--background"], ["--resume"], ["--install-autostart"]])
def test_a_start_on_a_settings_file_that_does_not_exist_says_so_as_status_does(tmp_path, monkeypatch, capsys, options):
    monkeypatch.setattr(start, "_windows", lambda: True)
    monkeypatch.setattr(start, "install_autostart", lambda *args, **kwargs: pytest.fail("installed"))
    settings = tmp_path / "live" / "settings.json"
    settings.parent.mkdir()
    assert start.main(["--settings", str(settings), *options]) == 2
    out = capsys.readouterr()
    assert out.out == f"the settings file does not exist: {settings}\n" and out.err == ""
    assert list(settings.parent.iterdir()) == []                # nothing written


# --- Ctrl-C outside the supervision ---

def with_fakes(monkeypatch, name: str, **fakes) -> None:
    """The command's own call of start.<name>, with these fakes added (the clock, the sleep)."""
    real = getattr(start, name)
    monkeypatch.setattr(start, name, lambda *args, **kwargs: real(*args, **{**kwargs, **fakes}))


def command(*argv: str) -> int:
    """start.main; a KeyboardInterrupt that leaves it is a failure (pytest would take it for its own interruption and end the run)."""
    try:
        return start.main(list(argv))
    except KeyboardInterrupt:
        pytest.fail("the command ended with a KeyboardInterrupt")


def test_ctrl_c_during_the_watch_of_a_start_ends_the_command_with_one_line_and_130(folder, monkeypatch, capsys):
    example_settings(folder)
    clock = Clock()
    write_state(folder, clock.now - 3)
    with_fakes(monkeypatch, "supervise", spawn=Spawn(clock), clock=clock, sleep=interrupted_at(clock, 3), wall=clock, tie=no_tie)
    assert command("--settings", str(folder / "settings.json")) == 130
    out = capsys.readouterr()
    assert out.out.splitlines() == [WATCHING, "Ctrl-C: nothing was started"]
    assert "Traceback" not in out.out + out.err + log(folder)


def test_ctrl_c_during_the_watch_of_a_background_start_ends_the_command_with_one_line_and_130(folder, monkeypatch, capsys):
    example_settings(folder)
    clock = Clock()
    write_state(folder, clock.now - 3)
    with_fakes(monkeypatch, "background", spawn=lambda *a, **k: pytest.fail("started"), clock=clock, sleep=interrupted_at(clock, 3),
               wall=clock)
    assert command("--settings", str(folder / "settings.json"), "--background") == 130
    assert capsys.readouterr().out.splitlines()[-1] == "Ctrl-C: nothing was started"


def test_ctrl_c_during_the_wait_of_stop_says_that_the_stop_file_stays_and_ends_with_130(folder, monkeypatch, capsys):
    clock = Clock()
    beating(clock, folder, seconds=60)
    with_fakes(monkeypatch, "stop", sleep=interrupted_at(clock, 3), wall=clock, clock=clock)
    assert command("--settings", str(folder / "settings.json"), "--stop") == 130
    out = capsys.readouterr()
    assert out.out.splitlines()[-2:] == ["waiting for the parts to end (up to 90 seconds)",
                                         "Ctrl-C: the STOP file stays, and the launcher goes on stopping the parts; check with --status"]
    assert "Traceback" not in out.out + out.err and (folder / "STOP").exists()


def test_ctrl_c_during_the_wait_of_resume_says_that_nothing_was_started_and_ends_with_130(folder, monkeypatch, capsys):
    example_settings(folder)
    clock = Clock()
    (folder / "STOP").write_text("", encoding="utf-8")
    beating(clock, folder, seconds=60)
    with_fakes(monkeypatch, "resume", sleep=interrupted_at(clock, 3), wall=clock, clock=clock)
    monkeypatch.setattr(start, "supervise", lambda *args, **kwargs: pytest.fail("started"))
    assert command("--settings", str(folder / "settings.json"), "--resume") == 130
    out = capsys.readouterr()
    assert out.out.splitlines()[-1] == "Ctrl-C: nothing was started" and "Traceback" not in out.out + out.err
    assert (folder / "STOP").exists()


def launcher_values(folder: Path) -> dict:
    return start.autostart_shortcut(folder / "settings.json", True, Path(PY), 5, startup=folder / "Startup")


def test_background_makes_the_checks_in_the_foreground_and_then_starts_the_launcher_detached(folder, monkeypatch, capsys):
    monkeypatch.setattr(start, "_windows", lambda: False)
    clock = Clock()
    spawned = []

    def spawn(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        return Proc(clock)
    opener = answering()
    assert start.background(folder / "settings.json", True, 5, PY, spawn=spawn, sleep=clock.sleep, clock=clock, wall=clock,
                            opener=opener) == 0
    assert opener.asked == [(REVIEW, 2), (CAPTURE, 2)]
    (cmd, kwargs), = spawned
    assert cmd == [str(Path(PY)), "-m", "aid.start", "--settings", str((folder / "settings.json").resolve()), "--testshop",
                   "--poll-seconds", "5"]
    assert kwargs["cwd"] == str(REPO_ROOT) and kwargs["start_new_session"] is True and not kwargs.get("creationflags")
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "background" in out[0] and str(folder / "logs" / "start.log") in out[0]
    assert WINDOW not in out[0]


@pytest.mark.parametrize("reason", ["stop", "running", "answers"])
def test_background_starts_nothing_when_a_check_refuses_and_says_why_in_the_foreground(folder, capsys, reason):
    clock = Clock()
    opener = answering()
    if reason == "stop":
        (folder / "STOP").write_text("", encoding="utf-8")
    elif reason == "running":
        beating(clock, folder, seconds=30)
    else:
        opener = answering(7870)
    spawned = []
    assert start.background(folder / "settings.json", False, None, PY, spawn=lambda cmd, **kwargs: spawned.append(cmd),
                            sleep=clock.sleep, clock=clock, wall=clock, opener=opener) == 3
    assert spawned == []
    out = capsys.readouterr().out
    assert {"stop": "stopped by the STOP file", "running": "already running", "answers": f"already answers at {CAPTURE}"}[reason] in out


def test_background_says_so_when_the_launcher_cannot_be_started(folder, capsys):
    clock = Clock()

    def spawn(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file", cmd[0])
    assert start.background(folder / "settings.json", False, None, PY, spawn=spawn, sleep=clock.sleep, clock=clock, wall=clock) == 1
    assert "could not be started in the background (FileNotFoundError" in capsys.readouterr().out


def test_background_removes_the_state_file_of_a_launcher_that_no_longer_runs_so_that_the_launcher_does_not_watch_again(folder, capsys):
    """One watch: the command in the window establishes the leftover, removes it and says so; the launcher it starts finds no state
    file. "It is replaced" is the line of the launcher that replaces the file, so it is not written here."""
    clock = Clock()
    write_state(folder, clock.now - 3)                         # fresh, and it does not move
    spawned = []
    assert start.background(folder / "settings.json", False, None, PY, spawn=lambda cmd, **kw: spawned.append(cmd), sleep=clock.sleep,
                            clock=clock, wall=clock) == 0
    assert len(spawned) == 1 and clock.now - 1000 == 7 and not (folder / start.STATE_FILE).exists()
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [WATCHING, "found the state file (start_state.json) of a launcher that no longer runs: it is removed"]
    assert "replaced" not in log(folder)


def test_background_gives_a_runner_left_behind_its_time_in_the_window_and_the_launcher_it_starts_finds_nothing_to_wait_for(folder):
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(11)}, runner_poll=10)
    spawned = []

    def spawn(cmd, **kwargs):
        spawned.append(((folder / "STOP").exists(), (folder / start.STATE_FILE).exists()))
    assert start.background(folder / "settings.json", False, None, PY, spawn=spawn, sleep=clock.sleep, clock=clock, wall=clock) == 0
    assert clock.now - 1000 == 15 and spawned == [(False, False)]
    assert "is given 15 seconds to end" in log(folder)


def test_background_leaves_the_leftover_state_file_when_something_answers(folder):
    """Its process ids are the way to close the parts it names."""
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"review": part(12, command="aid.review")})
    assert start.background(folder / "settings.json", False, None, PY, spawn=lambda cmd, **kw: pytest.fail("started"),
                            sleep=clock.sleep, clock=clock, wall=clock, opener=answering(7860)) == 3
    assert read_state(folder)["parts"]["review"]["pid"] == 12


def test_the_detached_start_on_windows_first_leaves_the_job_of_its_window_and_tries_once_more_without(folder, monkeypatch,
                                                                                                     windows_numbers):
    monkeypatch.setattr(start, "_windows", lambda: True)
    values = launcher_values(folder)
    calls = []

    def spawn(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")       # the job of the window does not let a process leave it
        return "the launcher"
    assert start.start_detached(values, spawn) == "the launcher"
    (first, one), (second, two) = calls
    assert first == second == [values["target"], *values["argv"]]
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, first with CREATE_BREAKAWAY_FROM_JOB
    assert one["creationflags"] == 0x01000208 and two["creationflags"] == 0x00000208
    for kwargs in (one, two):
        assert kwargs["cwd"] == values["working_folder"] and not kwargs.get("start_new_session")
        assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL


def test_the_detached_start_on_windows_is_tried_twice_and_no_more(folder, monkeypatch, windows_numbers):
    monkeypatch.setattr(start, "_windows", lambda: True)
    calls = []

    def spawn(cmd, **kwargs):
        calls.append(kwargs["creationflags"])
        raise PermissionError(13, "Access is denied")
    with pytest.raises(PermissionError):
        start.start_detached(launcher_values(folder), spawn)
    assert calls == [DETACHED | BREAKAWAY, DETACHED] == [0x01000208, 0x208]


def test_the_detached_start_on_windows_is_made_once_when_it_is_not_refused(folder, monkeypatch, windows_numbers):
    monkeypatch.setattr(start, "_windows", lambda: True)
    calls = []
    start.start_detached(launcher_values(folder), lambda cmd, **kwargs: calls.append(kwargs["creationflags"]))
    assert calls == [0x01000208]


def test_the_command_starts_in_the_window_or_in_the_background_as_asked(folder, monkeypatch):
    example_settings(folder)
    seen = []
    monkeypatch.setattr(start, "supervise", lambda settings, commands, **kwargs: seen.append(("window", kwargs)) or 0)
    monkeypatch.setattr(start, "background", lambda *args, **kwargs: seen.append(("background", args)) or 0)
    settings = str(folder / "settings.json")
    assert start.main(["--settings", settings, "--poll-seconds", "5"]) == 0
    assert start.main(["--settings", settings, "--testshop", "--poll-seconds", "5", "--background"]) == 0
    (first, kwargs), (second, args) = seen
    assert first == "window" and kwargs == {"poll_seconds": 5.0, "window": True, "ignore_after_stop": True}
    assert second == "background" and args == ((folder / "settings.json").resolve(), True, 5.0, sys.executable)


# --- the settings and the options ---

def test_a_settings_file_that_cannot_be_read_ends_with_2_naming_the_field_and_not_the_value(folder, monkeypatch, capsys):
    data = json.loads((REPO_ROOT / "aid" / "settings.example.json").read_text(encoding="utf-8"))
    data["digest"] = "secret-value-123"
    (folder / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(start, "supervise", lambda *a, **k: pytest.fail("started"))
    monkeypatch.setattr(start, "background", lambda *a, **k: pytest.fail("started"))
    assert start.main(["--settings", str(folder / "settings.json")]) == 2
    out = capsys.readouterr()
    text = out.out + out.err
    assert "digest" in text and "secret-value-123" not in text
    assert start.main(["--settings", str(folder / "settings.json"), "--background"]) == 2
    assert start.main(["--settings", str(folder / "missing.json")]) == 2
    assert f"the settings file does not exist: {folder / 'missing.json'}" in capsys.readouterr().out


def test_a_poll_time_that_is_not_positive_is_refused(folder, capsys):
    assert start.main(["--settings", str(folder / "settings.json"), "--poll-seconds", "0"]) == 2


# --- the single instance, the leftovers and the state ---

def test_a_heartbeat_of_19_seconds_that_still_moves_is_a_launcher_that_runs(folder, capsys):
    clock = Clock()
    write_state(folder, clock.now - 19)
    clock.hooks.append((clock.now + 4, lambda: write_state(folder, clock.now)))     # the launcher that runs writes its next one
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == []
    assert clock.now - 1000 == 7                               # read again after HEARTBEAT_EVERY + 2 seconds
    assert "already running since 2026-11-10T08:00:00" in capsys.readouterr().out
    assert read_state(folder)["launcher"]["heartbeat"] == 1004 # the running launcher's file is left alone


def test_a_heartbeat_of_19_seconds_that_does_not_move_is_a_leftover_and_is_replaced_with_one_line(folder):
    """A quick log off and on: the launcher that wrote the file is gone, its heartbeat is still fresh (and Windows ended the parts,
    which were tied to it)."""
    clock = Clock()
    write_state(folder, clock.now - 19, parts={"runner": part(11, tied=True)})
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 20)
    seen = {}
    clock.hooks.append((clock.now + 12, lambda: seen.update(read_state(folder))))
    assert run(folder, clock, spawn) == 0
    assert spawn.times("runner") == [1008] and len(spawn.calls) == 3         # the watch, then the claim's tick
    assert seen["launcher"]["heartbeat"] >= 1008 and seen["parts"]["runner"]["pid"] == spawn.procs["runner"][0].pid
    text = log(folder)
    assert text.count("left over") == 1 and "already running" not in text
    assert ("the state file (start_state.json) is left over from a launcher that no longer runs (its heartbeat does not move): "
            "it is replaced") in text


def test_the_watch_of_a_start_says_so_in_one_line_before_it_waits(folder, capsys):
    """Seven seconds without a line look like a window that hangs."""
    clock = Clock()
    write_state(folder, clock.now - 3)
    said = []
    clock.hooks.append((clock.now + 1, lambda: said.append(capsys.readouterr().out)))
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, Spawn(clock)) == 0
    assert said == [f"{WATCHING}\n"]


def test_a_heartbeat_that_never_moves_in_the_watch_is_always_a_leftover(folder):
    """The start of the watch and its deadline come from one reading of the clock, and the watch is judged at the deadline: a clock
    that moves on between two readings, with a sleep that wakes exactly when asked, cannot make a heartbeat that stands still look
    like one that moves (a start refused as "already running" while no launcher runs)."""
    clock = CreepingClock()
    write_state(folder, clock.now - 3)
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.calls) == 3
    text = log(folder)
    assert "already running" not in text and "left over" in text


def test_a_state_file_that_goes_while_it_is_watched_was_a_launcher_that_just_ended(folder):
    clock = Clock()
    write_state(folder, clock.now - 2)
    clock.hooks.append((clock.now + 3, (folder / start.STATE_FILE).unlink))
    spawn = Spawn(clock)
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.calls) == 3 and 4 <= spawn.times("runner")[0] - 1000 <= 8     # seen gone, then the claim's tick
    assert "left over" not in log(folder)


def test_a_heartbeat_of_21_seconds_is_a_leftover_at_once_and_is_replaced(folder):
    clock = Clock()
    write_state(folder, clock.now - 21)
    spawn = Spawn(clock)
    seen = {}
    clock.hooks.append((clock.now + 12, lambda: seen.update(read_state(folder))))
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, spawn) == 0
    assert len(spawn.calls) == 3 and spawn.times("runner") == [1001]      # no watch: it is older than twenty seconds; the claim
    assert seen["launcher"]["heartbeat"] >= 1010            # written every 5 seconds
    runner = seen["parts"]["runner"]
    assert runner["command"] == "aid.runner" and runner["pid"] == spawn.procs["runner"][0].pid
    assert runner["starts"] == 1 and runner["given_up"] is False and runner["started"]
    assert seen["parts"]["capture"]["command"] == "aid.capture_server"


def test_the_state_gives_a_process_id_only_while_the_part_runs(folder):
    clock = Clock()
    spawn = Spawn(clock, {"capture": [(2, 1), (float("inf"), 0)], "review": [(2, 2)]})
    seen = {}
    clock.hooks.append((clock.now + 4, lambda: seen.update(first=read_state(folder)["parts"])))    # capture waits to start again
    clock.hooks.append((clock.now + 12, lambda: seen.update(then=read_state(folder)["parts"])))
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, spawn) == 0
    first, then = seen["first"], seen["then"]
    assert first["capture"]["state"] == "waiting to start again" and first["capture"]["pid"] is None
    assert first["review"]["state"] == "ended (exit code 2)" and first["review"]["pid"] is None
    assert first["runner"]["state"] == "running" and first["runner"]["pid"] == spawn.procs["runner"][0].pid
    assert then["capture"]["state"] == "running" and then["capture"]["pid"] == spawn.procs["capture"][1].pid


def test_the_state_is_written_after_each_start_so_that_slow_starts_keep_the_heartbeat_fresh(folder):
    """Each start takes 6 seconds here. A launcher that is ended meanwhile leaves the process ids of the parts started so far."""
    clock = Clock()
    inner, seen = Spawn(clock), []

    def spawn(cmd, **kwargs):
        seen.append(read_state(folder))
        clock.sleep(6)
        return inner(cmd, **kwargs)
    stop_at(clock, folder, clock.now + 20)
    assert run(folder, clock, spawn) == 0
    before_the_third = seen[2]
    assert before_the_third["launcher"]["heartbeat"] == 1013                        # 1 (the claim's tick) and two starts of 6
    assert before_the_third["parts"]["runner"]["pid"] == inner.procs["runner"][0].pid
    assert before_the_third["parts"]["review"]["pid"] == inner.procs["review"][0].pid
    assert before_the_third["parts"]["capture"]["pid"] is None


def test_during_a_stop_the_state_says_at_once_which_parts_have_ended(folder):
    clock = Clock()
    spawn = Spawn(clock)                                       # the runner goes on until its wait is over
    stop_at(clock, folder, clock.now + 3)
    seen = {}
    clock.hooks.append((clock.now + 4, lambda: seen.update(read_state(folder)["parts"])))
    assert run(folder, clock, spawn, poll=20) == 0
    assert seen["review"]["state"] == "stopped" and seen["review"]["pid"] is None
    assert seen["capture"]["state"] == "stopped" and seen["capture"]["pid"] is None
    assert seen["runner"]["state"] == "running" and seen["runner"]["pid"] == spawn.procs["runner"][0].pid


def test_nothing_is_started_beside_parts_that_already_answer(folder, capsys):
    """Only one launcher runs on a PC, because the two addresses are fixed: the line says so instead of guessing the cause. The
    second line names the parts that a leftover state file records as running and that were not tied to its launcher."""
    clock = Clock()
    write_state(folder, clock.now - 60, parts={"runner": part(11, tied=True), "review": part(12, command="aid.review", tied=False),
                                               "capture": part(13, "given up", "aid.capture_server", tied=False)})
    before = (folder / start.STATE_FILE).read_text(encoding="utf-8")
    spawn = Spawn(clock)
    opener = answering(7860)
    assert run(folder, clock, spawn, opener=opener) == 3
    assert spawn.calls == []
    assert opener.asked == [(REVIEW, 2), (CAPTURE, 2)]         # with the opener and the limit of --status
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [f"something already answers at {REVIEW}: {ONE_LAUNCHER}",
                     "the state file (start_state.json) of the last launcher names as running: review (process 12)"]
    assert (folder / start.STATE_FILE).read_text(encoding="utf-8") == before      # the leftover keeps its process ids
    assert "something already answers" in log(folder)


def test_a_launcher_that_starts_while_the_addresses_are_asked_is_seen(folder, capsys):
    """Asking two addresses that do not answer takes seconds on Windows: two launchers started in that time must not both go on."""
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(11)})        # a leftover of long ago

    def opener(url, timeout):                                               # meanwhile another launcher starts and writes its state
        (folder / start.STATE_FILE).write_text(json.dumps({
            "launcher": {"pid": 8, "started": "2026-11-10T09:00:00", "heartbeat": clock.now, "heartbeat_at": "x"}, "parts": {}}),
            encoding="utf-8")
        raise urllib.error.URLError("refused")
    spawn = Spawn(clock)
    assert run(folder, clock, spawn, opener=opener) == 3
    assert spawn.calls == [] and read_state(folder)["launcher"]["pid"] == 8
    assert "already running since 2026-11-10T09:00:00" in capsys.readouterr().out


def test_parts_that_answer_without_a_leftover_state_give_one_line(folder, capsys):
    clock = Clock()
    spawn = Spawn(clock)
    assert run(folder, clock, spawn, opener=answering(7860, 7870)) == 3
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [f"something already answers at {REVIEW} and {CAPTURE}: {ONE_LAUNCHER}"]
    assert spawn.calls == [] and not (folder / start.STATE_FILE).exists()


def test_a_launcher_claims_its_folder_and_waits_one_tick_before_it_starts_a_part(folder):
    """The state file names this launcher (its process id and start) before any part is started."""
    clock = Clock()
    spawn = Spawn(clock)
    seen = []
    clock.hooks.append((clock.now + 0.5, lambda: seen.append((read_state(folder)["launcher"]["pid"], len(spawn.calls)))))
    stop_at(clock, folder, clock.now + 5)
    assert run(folder, clock, spawn) == 0
    assert seen == [(os.getpid(), 0)] and spawn.times("runner") == [1001]


def test_a_launcher_that_finds_another_launcher_in_the_state_file_after_its_claim_starts_nothing(folder, capsys):
    """Two launchers started in the same moment both pass the checks; each writes its claim, waits one tick and reads the file
    back. The one whose claim was overwritten starts nothing, and leaves the other one's file alone."""
    clock = Clock()

    def another():
        (folder / start.STATE_FILE).write_text(json.dumps({
            "launcher": {"pid": 8, "started": "2026-11-10T09:00:00", "heartbeat": clock.now, "heartbeat_at": "x"}, "parts": {}}),
            encoding="utf-8")
    clock.hooks.append((clock.now + 0.5, another))
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == [] and read_state(folder)["launcher"]["pid"] == 8
    assert "a launcher was started on these settings meanwhile; nothing started" in capsys.readouterr().out


def test_a_launcher_whose_claim_cannot_be_written_starts_nothing_and_says_why(folder, monkeypatch, capsys):
    def replace(source, target):
        raise PermissionError(13, "the file is in use")
    monkeypatch.setattr(start.os, "replace", replace)
    clock = Clock()
    spawn = Spawn(clock)
    assert run(folder, clock, spawn) == 3
    assert spawn.calls == [] and list((folder).glob(".start_state*")) == []      # no temporary file is left either
    assert "the state file (start_state.json) could not be written (PermissionError): nothing started" in capsys.readouterr().out


def test_every_write_of_the_state_file_goes_through_a_temporary_file_with_a_name_of_its_own(folder, monkeypatch):
    """Two launchers that write in the same moment do not take each other's temporary file."""
    real, sources = os.replace, []

    def replace(source, target):
        if Path(target).name == start.STATE_FILE:
            sources.append(Path(source).name)
        return real(source, target)
    monkeypatch.setattr(start.os, "replace", replace)
    clock = Clock()
    stop_at(clock, folder, clock.now + 12)
    assert run(folder, clock, Spawn(clock)) == 0
    assert len(sources) >= 5 and len(set(sources)) == 1
    assert str(os.getpid()) in sources[0] and sources[0] != f".{start.STATE_FILE}.tmp"


def test_the_launcher_never_uses_os_kill():
    text = (REPO_ROOT / "aid" / "start.py").read_text(encoding="utf-8")
    assert "os.kill" not in text and "kill(" not in text.replace(".kill()", "")


def test_status_reads_the_state_and_asks_the_two_addresses(folder, capsys):
    clock = Clock()
    clock.now = time.time()
    (folder / start.STATE_FILE).write_text(json.dumps({
        "launcher": {"started": "2026-11-10T08:00:00", "heartbeat": clock.now - 3, "pid": 7},
        "parts": {"runner": {"command": "aid.runner", "pid": 11, "started": "2026-11-10T08:00:01", "starts": 1, "given_up": False,
                             "state": "running"},
                  "review": {"command": "aid.review", "pid": 12, "started": "2026-11-10T08:00:01", "starts": 2, "given_up": False,
                             "state": "running"},
                  "capture": {"command": "aid.capture_server", "pid": None, "started": "2026-11-10T08:00:01", "starts": 6,
                              "given_up": True, "state": "given up"}}}), encoding="utf-8")
    opener = answering(7860)
    assert start.status(folder / "settings.json", opener=opener, wall=clock) == 0
    out = capsys.readouterr().out
    assert opener.asked == [(REVIEW, 2), (CAPTURE, 2)]
    lines = out.splitlines()
    assert "running since 2026-11-10T08:00:00" in lines[0]
    assert lines[1].startswith("runner: running, process 11,")
    assert lines[2].startswith("review: running, process 12,") and lines[2].endswith(f"{REVIEW} answers")
    assert lines[3].startswith("capture: given up") and lines[3].endswith(f"{CAPTURE} does not answer")
    assert "process" not in lines[3] and "leftover" not in out   # a process id only while the part runs


def test_status_names_a_process_only_for_a_part_that_runs_also_in_a_state_file_of_before(folder, capsys):
    """An earlier launcher kept the process id of a part that had ended; it is not shown, so that nobody ends another process."""
    clock = Clock()
    write_state(folder, clock.now - 3, parts={"runner": part(11), "review": part(12, "waiting to start again", "aid.review"),
                                              "capture": part(13, "given up", "aid.capture_server")})
    assert start.status(folder / "settings.json", opener=refused, wall=clock) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "process 11" in lines[1] and "process" not in lines[2] and "process" not in lines[3]


@pytest.mark.parametrize("age, code", [(19, 0), (19.9, 0), (20, 1), (20.1, 1), (21, 1)])
def test_status_takes_a_heartbeat_younger_than_twenty_seconds_for_a_running_launcher(folder, capsys, age, code):
    clock = Clock()
    write_state(folder, clock.now - age)
    assert start.status(folder / "settings.json", opener=refused, wall=clock) == code
    assert ("launcher: running since" in capsys.readouterr().out) is (code == 0)


def test_status_without_a_launcher_exits_1(folder, capsys):
    assert start.status(folder / "settings.json", opener=refused) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "launcher: not running"
    assert lines[1] == "runner: not started by a launcher"


def test_status_shows_the_parts_a_launcher_left_behind(folder, capsys):
    """The launcher was ended from outside and its parts were not tied to it: its state file still names them. A review page or a
    capture server is a leftover only while its address answers; the runner has no address, and may still run until STOP."""
    clock = Clock()
    write_state(folder, clock.now - 300, heartbeat_at="2026-11-10T08:35:49",
                parts={"runner": part(11), "review": part(12, command="aid.review", tied=False),
                       "capture": part(13, "given up", "aid.capture_server")})
    (folder / "STOP").write_text("", encoding="utf-8")
    assert start.status(folder / "settings.json", opener=answering(7860), wall=clock) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == ("launcher: not running; it ended without stopping its parts (last heartbeat 2026-11-10T08:35:49) "
                        "(the STOP file exists)")
    assert lines[1] == "runner: may still run (process 11); it obeys the STOP file"
    assert lines[2].startswith("review: leftover, process 12") and lines[2].endswith(f"{REVIEW} answers")
    assert "leftover" not in lines[3] and "process" not in lines[3] and lines[3].endswith(f"{CAPTURE} does not answer")


def test_status_calls_an_untied_page_whose_address_does_not_answer_ended(folder, capsys):
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"review": part(12, command="aid.review"),
                                                "capture": part(13, command="aid.capture_server")})
    assert start.status(folder / "settings.json", opener=answering(7870), wall=clock) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[2] == f"review: ended; {REVIEW} does not answer"
    assert lines[3].startswith("capture: leftover, process 13") and lines[3].endswith(f"{CAPTURE} answers")


def test_status_after_an_end_without_a_stop_on_windows_says_that_the_tied_parts_ended_with_the_launcher(folder, capsys):
    """A closed window, Task Manager or a logoff: Windows ended the parts with the launcher, and the state file stays behind."""
    clock = Clock()
    write_state(folder, clock.now - 300, heartbeat_at="2026-11-10T08:35:49",
                parts={"runner": part(11, tied=True), "review": part(12, command="aid.review", tied=True),
                       "capture": part(13, command="aid.capture_server", tied=True)})
    assert start.status(folder / "settings.json", opener=refused, wall=clock) == 1
    assert capsys.readouterr().out.splitlines() == [
        "launcher: not running; it was ended from outside (last heartbeat 2026-11-10T08:35:49), and its parts ended with it",
        "runner: ended with the launcher",
        f"review: ended with the launcher; {REVIEW} does not answer",
        f"capture: ended with the launcher; {CAPTURE} does not answer"]


def test_status_of_a_stale_state_without_running_parts_is_plain(folder, capsys):
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"runner": part(None, "stopped"), "capture": part(None, "given up", "aid.capture_server")})
    assert start.status(folder / "settings.json", opener=refused, wall=clock) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "launcher: not running" and "leftover" not in out


def test_a_state_file_that_cannot_be_removed_is_tried_once_more(folder, monkeypatch):
    clock = Clock()
    stop_at(clock, folder, clock.now + 3)
    real, failures = Path.unlink, {"left": 1}

    def unlink(self, *args, **kwargs):
        if self.name == start.STATE_FILE and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(13, "the file is in use")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    assert run(folder, clock, Spawn(clock)) == 0
    assert failures["left"] == 0 and not (folder / start.STATE_FILE).exists()
    assert "could not be removed" not in log(folder)


def test_a_state_file_that_still_cannot_be_removed_is_one_line(folder, monkeypatch):
    clock = Clock()
    stop_at(clock, folder, clock.now + 3)
    real, tried = Path.unlink, []

    def unlink(self, *args, **kwargs):
        if self.name == start.STATE_FILE:
            tried.append(clock.now)
            raise PermissionError(13, "the file is in use")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    assert run(folder, clock, Spawn(clock)) == 0
    assert len(tried) == 2 and tried[1] > tried[0]              # once more, after a short sleep
    text = log(folder)
    assert text.count("the state file (start_state.json) could not be removed (PermissionError)") == 1
    assert text.rstrip().endswith("stopped")


# --- the start at logon ---

def python_with_spaces(tmp_path: Path, pythonw: bool) -> Path:
    bin_dir = tmp_path / "Program Files" / "Python 311"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python.exe"
    python.write_text("", encoding="utf-8")
    if pythonw:
        (bin_dir / "pythonw.exe").write_text("", encoding="utf-8")
    return python


def registry(value=None, key: bool = True, expanded: dict | None = None):
    """A fake winreg: the value Startup of the user's shell folders (None: no such value; key False: no such key)."""
    asked = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(root, path):
        asked.append((root, path))
        if not key:
            raise FileNotFoundError(2, "the key does not exist")
        return Key()

    def query(opened, name):
        asked.append(name)
        if value is None:
            raise FileNotFoundError(2, "the value does not exist")
        return value, 2

    def expand(text):
        for name, to in (expanded or {}).items():
            text = text.replace(f"%{name}%", to)
        return text
    return types.SimpleNamespace(HKEY_CURRENT_USER="HKCU", OpenKey=open_key, QueryValueEx=query, ExpandEnvironmentStrings=expand,
                                 asked=asked)


def test_the_startup_folder_is_the_one_the_start_menu_itself_uses(tmp_path, monkeypatch):
    """A Start Menu that the organisation moved elsewhere is followed."""
    fake = registry("%MOVED%\\Start Menu\\Programs\\Startup", expanded={"MOVED": str(tmp_path / "moved")})
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert start.startup_folder() == Path(str(tmp_path / "moved") + "\\Start Menu\\Programs\\Startup")
    assert fake.asked == [("HKCU", "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders"), "Startup"]


@pytest.mark.parametrize("fake", [registry(key=False), registry(None), registry(""), registry("%NOT_SET%\\Startup"), registry(7), None],
                         ids=["no key", "no value", "empty", "not expanded", "no text", "no winreg"])
def test_the_startup_folder_is_under_appdata_when_the_registry_does_not_give_it(tmp_path, monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "winreg", fake)           # None: the import fails, as on a system that is not Windows
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert start.startup_folder() == tmp_path / "AppData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def test_the_shortcut_runs_pythonw_beside_the_python_with_the_absolute_settings(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    python = python_with_spaces(tmp_path, pythonw=True)
    settings = tmp_path / "my settings" / "settings.json"
    values = start.autostart_shortcut(settings, True, python, poll_seconds=30)
    assert values["target"] == str(python.parent / "pythonw.exe")
    assert values["arguments"] == f'-m aid.start --settings "{settings.resolve()}" --testshop --poll-seconds 30'
    assert values["argv"] == ["-m", "aid.start", "--settings", str(settings.resolve()), "--testshop", "--poll-seconds", "30"]
    assert values["working_folder"] == str(REPO_ROOT)
    assert values["path"] == tmp_path / "AppData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Inkoopassistent.lnk"
    plain = start.autostart_shortcut(settings, False, python, startup=tmp_path / "Startup")
    assert plain["arguments"] == f'-m aid.start --settings "{settings.resolve()}"'
    assert plain["path"] == tmp_path / "Startup" / "Inkoopassistent.lnk"


def test_the_shortcut_takes_the_python_itself_without_a_pythonw(tmp_path):
    python = python_with_spaces(tmp_path, pythonw=False)
    values = start.autostart_shortcut(tmp_path / "settings.json", False, python, startup=tmp_path)
    assert values["target"] == str(python)


LAST = "Log off and on once, then check with --status that the launcher started"


@pytest.fixture
def windows(folder, tmp_path, monkeypatch, windows_numbers):
    """A Windows of the tests: start._windows says so, subprocess has the creation flags of Windows, the registry gives no Startup
    folder and APPDATA is under tmp_path, so the folder is the one returned; the settings are the example's."""
    monkeypatch.setattr(start, "_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "winreg", registry(key=False))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    example_settings(folder)
    return tmp_path / "AppData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def makes_the_shortcut(ran: list | None = None):
    """A PowerShell that makes the shortcut."""
    def powershell(cmd, **kwargs):
        if ran is not None:
            ran.append((cmd, kwargs))
        Path(kwargs["env"]["AID_LNK"]).write_text("lnk", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return powershell


def install(folder: Path, python: Path, clock: Clock | None = None, **more) -> int:
    clock = clock or Clock()
    more.setdefault("run", makes_the_shortcut())
    more.setdefault("spawn", lambda cmd, **kwargs: None)
    return start.install_autostart(folder / "settings.json", more.pop("testshop", False), None, python, wall=clock, sleep=clock.sleep,
                                   clock=clock, **more)


def test_install_makes_the_shortcut_through_powershell_with_its_values_in_the_environment(folder, windows, tmp_path, capsys):
    python = python_with_spaces(tmp_path, pythonw=True)
    ran, spawned = [], []

    def spawn(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        return Proc(Clock(), float("inf"), 0)
    assert install(folder, python, testshop=True, run=makes_the_shortcut(ran), spawn=spawn) == 0
    (cmd, kwargs), = ran
    # no -ExecutionPolicy: the policy does not apply to a -Command text, and the option reads as an evasion
    assert cmd[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"] and len(cmd) == 5
    assert cmd[4] == start.POWERSHELL_SCRIPT
    assert "ExecutionPolicy" not in " ".join(cmd) and "Bypass" not in " ".join(cmd)
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["creationflags"] == NO_WINDOW     # no console window flashes
    env = kwargs["env"]
    values = start.autostart_shortcut(folder / "settings.json", True, python)
    assert env["AID_LNK"] == str(windows / "Inkoopassistent.lnk")
    assert env["AID_TARGET"] == values["target"] and env["AID_ARGS"] == values["arguments"] and env["AID_CWD"] == values["working_folder"]
    text = " ".join(cmd)
    for value in (env["AID_LNK"], env["AID_TARGET"], env["AID_ARGS"], env["AID_CWD"], str(folder), "Program Files", "AppData"):
        assert value not in text
    assert "WScript.Shell" in text and "$env:AID_TARGET" in text
    assert (windows / "Inkoopassistent.lnk").exists() and not (windows / "Inkoopassistent.cmd").exists()
    (launched, popen), = spawned                                 # the detached start of --background
    assert launched == [values["target"], *values["argv"]]
    assert popen["cwd"] == str(REPO_ROOT)
    assert popen["creationflags"] == 0x01000208                  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    assert popen["stdin"] == popen["stdout"] == popen["stderr"] == subprocess.DEVNULL
    lines = capsys.readouterr().out.strip().splitlines()
    out = "\n".join(lines)
    assert "Inkoopassistent.lnk" in out and "start.log" in out and "started" in out
    assert lines[-1] == LAST


def test_the_fixed_script_reads_the_saved_shortcut_back_and_deletes_one_whose_values_differ():
    script = start.POWERSHELL_SCRIPT
    assert script.count("$shell.CreateShortcut($env:AID_LNK)") == 2          # the second one reads the saved file back
    assert script.index("$link.Save()") < script.index("$saved = $shell.CreateShortcut($env:AID_LNK)")
    for name, variable in (("TargetPath", "AID_TARGET"), ("Arguments", "AID_ARGS"), ("WorkingDirectory", "AID_CWD")):
        assert f"$saved.{name} -ine $env:{variable}" in script              # without regard to case
    check = script[script.index("$saved ="):]
    assert check.count("-or") == 2
    assert "Remove-Item -LiteralPath $env:AID_LNK" in check and check.rstrip(" }").endswith("exit 1")
    assert check.index("Remove-Item") < check.index("exit 1")
    assert '"' not in script                                                # no value and no double quote: nothing to escape


@pytest.mark.parametrize("failure", ["missing", "fails", "lost characters"])
def test_install_writes_a_cmd_file_when_powershell_cannot_make_the_shortcut(folder, windows, tmp_path, capsys, failure):
    python = python_with_spaces(tmp_path, pythonw=True)

    def powershell(cmd, **kwargs):
        if failure == "missing":
            raise FileNotFoundError(2, "No such file", "powershell")
        if failure == "fails":
            Path(kwargs["env"]["AID_LNK"]).write_text("half a shortcut", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 1, "", "error")     # also the script's own exit 1 after the read-back
    spawned = []
    assert install(folder, python, run=powershell, spawn=lambda cmd, **kw: spawned.append(cmd)) == 0
    assert not (windows / "Inkoopassistent.lnk").exists()
    values = start.autostart_shortcut(folder / "settings.json", False, python)
    content = (windows / "Inkoopassistent.cmd").read_bytes().decode("utf-8")       # ASCII, unless the test's own folder is not
    assert f'pushd "{values["working_folder"]}"' in content and "cd /d" not in content
    assert f'start "" "{values["target"]}" {values["arguments"]}' in content
    lines = capsys.readouterr().out.strip().splitlines()
    assert "Inkoopassistent.cmd" in lines[0]
    assert lines[-1] == LAST
    assert len(spawned) == 1


def cmd_values(working_folder: str, target: str = "C:\\Program Files\\Python 311\\pythonw.exe",
               settings: str = "C:\\live\\settings.json") -> dict:
    return {"working_folder": working_folder, "target": target, "arguments": f'-m aid.start --settings "{settings}"'}


def test_the_cmd_file_is_ascii_when_every_character_is(tmp_path):
    """A percent sign is doubled wherever it stands (cmd.exe would read %...% as a variable): in the working folder, in the target
    and in the settings path of the arguments."""
    path = tmp_path / "Inkoopassistent.cmd"
    start._write_cmd(cmd_values("C:\\R&D 100% (new)\\repo", target="C:\\Python 3%11\\pythonw.exe",
                                settings="C:\\live 50%\\settings.json"), path)
    lines = path.read_bytes().decode("ascii").split("\r\n")
    assert lines == ["@echo off",
                     "rem The start at logon of the checking aid, written by: python -m aid.start --install-autostart",
                     'pushd "C:\\R&D 100%% (new)\\repo"',
                     'start "" "C:\\Python 3%%11\\pythonw.exe" -m aid.start --settings "C:\\live 50%%\\settings.json"', ""]


def test_the_cmd_file_keeps_every_character_as_utf8_without_a_byte_order_mark(tmp_path):
    """A folder with characters the code page of the command window lacks: nothing is replaced, the file says its encoding."""
    path = tmp_path / "Inkoopassistent.cmd"
    start._write_cmd(cmd_values("C:\\Users\\name\\OneDrive\\文档\\Bachelor-End-Project",
                                settings="C:\\Users\\Zoë\\live\\settings.json"), path)
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    lines = data.decode("utf-8").split("\r\n")
    assert lines[0] == "@echo off" and lines[1] == "chcp 65001 >nul"
    assert 'pushd "C:\\Users\\name\\OneDrive\\文档\\Bachelor-End-Project"' in lines
    assert lines[-2].endswith('--settings "C:\\Users\\Zoë\\live\\settings.json"')
    assert "?" not in data.decode("utf-8")


def test_the_cmd_file_is_not_written_when_a_character_cannot_be_written(tmp_path):
    path = tmp_path / "Inkoopassistent.cmd"
    with pytest.raises(UnicodeError):
        start._write_cmd(cmd_values("C:\\half a character \udcff"), path)
    assert not path.exists()


@pytest.mark.parametrize("why", ["no folder", "no cmd", "a character"])
def test_install_says_that_the_start_at_logon_could_not_be_set_and_ends_with_1(folder, windows, tmp_path, capsys, monkeypatch, why):
    """Also what it would run, so that a shortcut can be made by hand."""
    python = python_with_spaces(tmp_path, pythonw=True)
    if why == "no folder":                                      # the Startup folder cannot be made: a file has its name
        windows.parent.mkdir(parents=True)
        windows.write_text("not a folder", encoding="utf-8")
    else:
        def refuse(values, path):
            if why == "a character":                            # a path with a character that cannot be written
                raise UnicodeEncodeError("utf-8", "\udcff", 0, 1, "surrogates not allowed")
            raise PermissionError(13, "Access is denied")
        monkeypatch.setattr(start, "_write_cmd", refuse)
    spawned = []
    code = install(folder, python, run=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", ""),
                   spawn=lambda cmd, **kw: spawned.append(cmd))
    assert code == 1
    out = capsys.readouterr()
    lines = out.out.strip().splitlines()
    assert lines[0].startswith("The start at logon could not be set: ") and "Traceback" not in out.out + out.err
    values = start.autostart_shortcut(folder / "settings.json", False, python)
    assert lines[-1] == f"It runs: {values['target']} {values['arguments']}  (in {values['working_folder']})"
    assert "log off and on" not in out.out.lower()
    assert spawned == []                                        # nothing is started that would hide it until the next logon


def test_install_watches_a_fresh_heartbeat_and_starts_no_second_launcher(folder, windows, tmp_path, capsys):
    python = python_with_spaces(tmp_path, pythonw=True)
    clock = Clock()
    beating(clock, folder, seconds=30)
    spawned = []
    assert install(folder, python, clock, spawn=lambda cmd, **kw: spawned.append(cmd)) == 0
    assert spawned == [] and clock.now - 1000 == 7
    out = capsys.readouterr().out
    assert "already running" in out and (windows / "Inkoopassistent.lnk").exists()


def test_install_starts_the_launcher_when_the_fresh_heartbeat_is_a_leftover(folder, windows, tmp_path, capsys):
    """One watch: the command removes the leftover, so that the launcher it starts does not watch it again."""
    python = python_with_spaces(tmp_path, pythonw=True)
    clock = Clock()
    write_state(folder, clock.now - 5)
    spawned = []
    assert install(folder, python, clock, spawn=lambda cmd, **kw: spawned.append(((folder / start.STATE_FILE).exists(), cmd))) == 0
    assert [exists for exists, _ in spawned] == [False] and clock.now - 1000 == 7
    out = capsys.readouterr().out
    assert "found the state file (start_state.json) of a launcher that no longer runs: it is removed" in out and "replaced" not in out


def test_install_does_not_start_the_launcher_while_the_stop_file_exists(folder, windows, tmp_path, capsys):
    """The last line then names --resume --background first: the launcher at logon obeys STOP too."""
    python = python_with_spaces(tmp_path, pythonw=True)
    (folder / "STOP").write_text("", encoding="utf-8")
    spawned = []
    assert install(folder, python, spawn=lambda cmd, **kw: spawned.append(cmd)) == 0
    assert spawned == [] and (windows / "Inkoopassistent.lnk").exists()
    lines = capsys.readouterr().out.strip().splitlines()
    assert "STOP" in "\n".join(lines)
    assert lines[-1] == "After --resume --background, log off and on once, then check with --status that the launcher started"


def test_install_removes_a_stop_file_left_by_an_interrupted_stop_and_starts(folder, windows, tmp_path):
    python = python_with_spaces(tmp_path, pythonw=True)
    (folder / "STOP").write_text("launcher 2026-11-10T08:43:09", encoding="utf-8")
    spawned = []
    assert install(folder, python, spawn=lambda cmd, **kw: spawned.append(cmd)) == 0
    assert len(spawned) == 1 and not (folder / "STOP").exists()


def test_install_sets_the_start_at_logon_but_starts_nothing_beside_parts_that_already_answer(folder, windows, tmp_path, capsys):
    python = python_with_spaces(tmp_path, pythonw=True)
    clock = Clock()
    write_state(folder, clock.now - 300, parts={"review": part(12, command="aid.review")})
    spawned = []
    assert install(folder, python, clock, spawn=lambda cmd, **kw: spawned.append(cmd), opener=answering(7860)) == 0
    assert spawned == [] and (windows / "Inkoopassistent.lnk").exists()         # the start at logon stays set
    out = capsys.readouterr().out
    assert f"something already answers at {REVIEW}" in out and "review (process 12)" in out
    assert "--resume --background" in out and "log on again" in out


def test_install_says_so_when_the_launcher_cannot_be_started_now(folder, windows, tmp_path, capsys):
    python = python_with_spaces(tmp_path, pythonw=True)
    tried = []

    def spawn(cmd, **kwargs):
        tried.append(kwargs["creationflags"])
        raise PermissionError(13, "Access is denied")
    assert install(folder, python, spawn=spawn) == 0
    assert tried == [0x01000208, 0x208]
    assert "could not be started now (PermissionError" in capsys.readouterr().out


def test_install_and_remove_use_the_startup_folder_of_the_registry(folder, windows, tmp_path, monkeypatch, capsys):
    python = python_with_spaces(tmp_path, pythonw=True)
    moved = tmp_path / "moved" / "Startup"
    monkeypatch.setitem(sys.modules, "winreg", registry(str(moved)))
    assert install(folder, python) == 0
    assert (moved / "Inkoopassistent.lnk").exists() and not windows.exists()
    assert start.remove_autostart() == 0
    assert not (moved / "Inkoopassistent.lnk").exists()
    assert "Inkoopassistent.lnk" in capsys.readouterr().out


def test_remove_deletes_the_shortcut_and_the_cmd_file(windows, capsys):
    windows.mkdir(parents=True)
    (windows / "Inkoopassistent.lnk").write_text("", encoding="utf-8")
    (windows / "Inkoopassistent.cmd").write_text("", encoding="utf-8")
    (windows / "other.lnk").write_text("", encoding="utf-8")
    assert start.remove_autostart() == 0
    assert sorted(path.name for path in windows.iterdir()) == ["other.lnk"]
    assert "--stop" in capsys.readouterr().out


def test_remove_says_that_no_start_at_logon_was_set_only_when_nothing_was_kept(windows, capsys):
    """A shortcut that cannot be deleted is a start at logon that is still set."""
    (windows / "Inkoopassistent.lnk").mkdir(parents=True)      # a folder by that name: it cannot be deleted as a file
    assert start.remove_autostart() == 1
    lines = capsys.readouterr().out.strip().splitlines()
    assert not any("No start at logon was set" in line for line in lines)
    assert lines[0].startswith("Could not be removed: ") and "Inkoopassistent.lnk" in lines[0]
    assert start.remove_autostart(windows / "elsewhere") == 0
    assert capsys.readouterr().out.startswith("No start at logon was set")


@pytest.mark.parametrize("option", ["--install-autostart", "--remove-autostart"])
def test_the_start_at_logon_is_for_windows_only(folder, monkeypatch, capsys, option):
    monkeypatch.setattr(start, "_windows", lambda: False)
    assert start.main(["--settings", str(folder / "settings.json"), option]) == 2
    assert "Windows" in capsys.readouterr().out


def test_on_windows_the_command_sets_and_removes_the_start_at_logon(folder, monkeypatch):
    monkeypatch.setattr(start, "_windows", lambda: True)
    example_settings(folder)
    seen = []
    monkeypatch.setattr(start, "install_autostart", lambda *args, **kwargs: seen.append(("install", args, kwargs)) or 0)
    monkeypatch.setattr(start, "remove_autostart", lambda *args, **kwargs: seen.append(("remove", args, kwargs)) or 0)
    settings = folder / "settings.json"
    assert start.main(["--settings", str(settings), "--testshop", "--poll-seconds", "30", "--install-autostart"]) == 0
    assert start.main(["--settings", str(settings), "--remove-autostart"]) == 0
    assert seen == [("install", (settings.resolve(), True, 30.0, sys.executable), {}), ("remove", (), {})]
    (folder / "settings.json").write_text("{ not json", encoding="utf-8")
    assert start.main(["--settings", str(settings), "--install-autostart"]) == 2     # the settings are read first
    assert len(seen) == 2


def test_on_windows_is_read_from_os_name(monkeypatch):
    monkeypatch.setattr(start.os, "name", "nt")
    assert start._windows() is True
    monkeypatch.setattr(start.os, "name", "posix")
    assert start._windows() is False


# --- the real launcher ---

def _in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _answers(url: str) -> bool:
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=2) as answer:
            return answer.status == 200
    except (OSError, ValueError):
        return False


MACHINE_LOCK = Path(tempfile.gettempdir()) / "aid-test-start-real-launcher.lock"


def _take_the_machine() -> bool:
    """The lock of the machine for the time of a real-launcher test: the launcher and its parts take the fixed ports 7860 and 7870,
    so two runs of these tests at the same time (a second terminal, another check) would meet there. A file made with exclusive
    creation in the temporary folder, holding the process id of the run that took it; a lock whose run no longer exists (where
    /proc tells it: a run ended from outside) is taken over. False when another run holds it."""
    for _ in range(2):
        try:
            handle = os.open(MACHINE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = int(MACHINE_LOCK.read_text(encoding="ascii"))
            except (OSError, ValueError):                        # being written, or gone meanwhile: taken all the same
                return False
            if not (Path("/proc").is_dir() and not Path(f"/proc/{holder}").exists()):
                return False
            MACHINE_LOCK.unlink(missing_ok=True)
            continue
        os.write(handle, str(os.getpid()).encode("ascii"))
        os.close(handle)
        return True
    return False


def _give_back_the_machine() -> None:
    try:
        if MACHINE_LOCK.read_text(encoding="ascii") == str(os.getpid()):
            MACHINE_LOCK.unlink()
    except OSError:
        pass


class RealLauncher:
    """The real launcher as a subprocess on a prepared demo folder, with --testshop and a poll time of one second. The commands
    take no other port, so the run uses 7860 and 7870: it holds the lock of the machine for its time, and it is skipped when another
    run holds that lock or when either port is in use. The launcher gets the environment of the run without GRADIO_SERVER_PORT, so
    that its review page listens on 7860, where the launcher looks."""

    def __init__(self, tmp_path: Path):
        if not _take_the_machine():
            pytest.skip(f"another run of the real-launcher tests holds {MACHINE_LOCK}")
        try:
            if _in_use(7860) or _in_use(7870):
                pytest.skip("the review page (7860) or the capture server (7870) port is in use")
            from aid.demo import main as demo_main
            self.live, self.out = tmp_path / "live", tmp_path / "launcher.out"
            assert demo_main(["--out", str(self.live), "--prepare-only"]) == 0
        except BaseException:
            _give_back_the_machine()
            raise
        self.state_file = self.live / start.STATE_FILE
        self.env = {**{name: value for name, value in os.environ.items() if name != "GRADIO_SERVER_PORT"}, "PYTHONDONTWRITEBYTECODE": "1"}
        self.proc = None
        self.state: dict = {}

    def __enter__(self):
        with open(self.out, "wb") as out:
            self.proc = subprocess.Popen([sys.executable, "-B", "-m", "aid.start", "--settings", str(self.live / "settings.json"),
                                          "--testshop", "--poll-seconds", "1"],
                                         cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=self.env)
        return self

    def command(self, *options: str) -> subprocess.CompletedProcess:
        """Another command of the launcher on the same folder, as from a second window."""
        return subprocess.run([sys.executable, "-B", "-m", "aid.start", "--settings", str(self.live / "settings.json"), *options],
                              cwd=REPO_ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=self.env, timeout=150)

    def said(self) -> str:
        return self.out.read_text(encoding="utf-8", errors="replace")

    def wait_for_the_parts(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (_answers(CAPTURE) and _answers(REVIEW)):
            assert self.proc.poll() is None, self.said()
            time.sleep(0.5)
        assert _answers(CAPTURE) and _answers(REVIEW), self.said()
        self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        assert sorted(self.state["parts"]) == ["capture", "review", "runner"]
        assert all(part["state"] == "running" for part in self.state["parts"].values())

    def assert_nothing_is_left(self) -> None:
        assert not self.state_file.exists()
        text = (self.live / "logs" / "start.log").read_text(encoding="utf-8")
        for name in ("runner", "review", "capture"):
            assert f"{name} ended" in text
        assert text.rstrip().endswith("stopped")
        assert not _answers(CAPTURE) and not _answers(REVIEW)
        if Path("/proc").is_dir():
            assert not any(Path(f"/proc/{part['pid']}").exists() for part in self.state["parts"].values())

    def __exit__(self, *exc):
        """Whatever the test found: no launcher and no part is left running, and the lock of the machine is given back."""
        try:
            if self.proc.poll() is None:
                (self.live / "STOP").write_text("", encoding="utf-8")
                try:
                    self.proc.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            self.end_what_is_left()
        finally:
            _give_back_the_machine()
        return False

    def end_what_is_left(self) -> None:
        """The processes that the state file names now, or named when the parts first answered, if their command line holds this
        test's own folder (where /proc tells it): a process id alone may by now be another program's."""
        if not Path("/proc").is_dir():
            return
        pids = {part.get("pid") for part in self.state.get("parts", {}).values()}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            pids |= {part.get("pid") for part in state["parts"].values()} | {state["launcher"].get("pid")}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        ours = {str(self.live).encode(), str(self.live.resolve()).encode()}
        for pid in pids:
            try:
                if isinstance(pid, int) and any(folder in Path(f"/proc/{pid}/cmdline").read_bytes() for folder in ours):
                    os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_the_real_launcher_starts_the_three_parts_and_stop_ends_them(tmp_path):
    """And the commands of a second window read its state file as they should: --status sees it run and both addresses answer, a
    second start on the same settings is refused, and --stop ends it."""
    with RealLauncher(tmp_path) as launcher:
        launcher.wait_for_the_parts()
        status = launcher.command("--status")
        assert status.returncode == 0, status.stdout + status.stderr
        assert status.stdout.startswith("launcher: running since") and status.stdout.count(" answers\n") == 2, status.stdout
        second = launcher.command("--testshop", "--poll-seconds", "1")
        assert second.returncode == 3 and "already running since" in second.stdout, second.stdout + second.stderr
        stopped = launcher.command("--stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert stopped.stdout.splitlines()[-1].startswith("the parts have ended; start them again with --resume"), stopped.stdout
        assert launcher.proc.wait(timeout=120) == 0
        launcher.assert_nothing_is_left()
        assert (launcher.live / "STOP").read_text(encoding="utf-8") == ""     # the switch stays until --resume


@pytest.mark.skipif(os.name == "nt", reason="Windows sends no SIGTERM that a program can catch")
def test_the_real_launcher_stops_its_parts_on_sigterm(tmp_path):
    with RealLauncher(tmp_path) as launcher:
        launcher.wait_for_the_parts()
        launcher.proc.send_signal(signal.SIGTERM)
        assert launcher.proc.wait(timeout=120) == 0, launcher.said()
        launcher.assert_nothing_is_left()
        assert not (launcher.live / "STOP").exists()             # the STOP file of its own stop is gone again
        said = launcher.said()
        assert "SIGTERM: stopping the parts" in said and "Traceback" not in said
        assert "runner ended with exit code 0" in said           # the runner ended by itself, at its next pass
        runner_out = (launcher.live / "logs" / "runner.out").read_text(encoding="utf-8", errors="replace")
        assert "Traceback" not in runner_out and "KeyboardInterrupt" not in runner_out
        assert "stopped by" in runner_out
