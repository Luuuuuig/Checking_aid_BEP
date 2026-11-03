"""The freeze record: what the aid was when it was frozen, so that the held-out run can be shown to have run on that aid.

    python3 -m aid.freeze --settings settings.json [--out freeze_<date>.json]

freeze_record gathers:
- git_commit, the commit of HEAD (git rev-parse HEAD in the repository root), "unknown" when git is not available, does not answer
  within a minute or the folder is no repository, and git_problem says which (None when the commit is known); working_tree_clean,
  whether git status --porcelain shows nothing (None when it is not known);
- settings_sha256, the sha256 of the settings file, and settings, the settings themselves with the authorization limit shown as
  "set" or "unset" exactly as the audit shows it (aid/audit.py); nothing else is hidden;
- prompts, each prompt file of aid/readers/prompts: its path, the version marker of its first line and its sha256;
- model, the model id of the settings;
- rules, the routing rules R01 to R11, each with the settings it reads (RULE_SETTINGS of aid/routing.py) and their values, the
  authorization limit again as "set" or "unset";
- aid_version (the default of Deps.aid_version), python, the Python version, and packages: the lines of `pip freeze`, or, when pip
  is not available or does not answer within a minute, the installed distributions as importlib.metadata lists them;
  packages_source says which, and packages_problem why pip was not used (None when it was);
- timestamp, the time of the clock.

The command writes the record as JSON (indent 2, sorted keys) to a temporary file beside --out, renames it into place when it is
complete and prints its path and the commit. A settings file that is missing or does not parse is a command error (exit 2).
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from importlib import metadata
from pathlib import Path

from aid.audit import _settings_dump
from aid.pipeline import _PROMPT_VERSION, Deps
from aid.routing import FALLBACK_RULE, RULE_SETTINGS
from aid.settings import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = Path("aid") / "readers" / "prompts"
PIP_FREEZE = [sys.executable, "-m", "pip", "freeze"]
TIMEOUT = 60                                     # seconds a git or pip command may take


def now() -> datetime:
    """The time of the freeze, from the clock; a function of its own so that a test can fix it."""
    return datetime.now().astimezone()


def freeze_record(settings_path: Path, settings: Settings, repo_root: Path, clock: Callable[[], datetime]) -> dict:
    """The freeze record (see the module docstring) of the settings read from `settings_path`, for the repository at `repo_root`."""
    commit, git_problem = _git(repo_root, "rev-parse", "HEAD")
    status, _ = _git(repo_root, "status", "--porcelain") if commit is not None else (None, None)
    dumped = _settings_dump(settings)
    packages_source, packages, packages_problem = _packages()
    return {
        "git_commit": commit or "unknown",
        "git_problem": git_problem,
        "working_tree_clean": None if status is None else status == "",
        "settings_sha256": _sha256(Path(settings_path)),
        "settings": dumped,
        "prompts": [_prompt(path, repo_root) for path in sorted((repo_root / PROMPTS).glob("*.md"))],
        "model": settings.model,
        "rules": {rule: {"settings": {name: dumped[name] for name in names}}
                  for rule, names in sorted(RULE_SETTINGS.items()) if rule != FALLBACK_RULE},
        "aid_version": Deps.model_fields["aid_version"].default,
        "python": platform.python_version(),
        "packages_source": packages_source,
        "packages": packages,
        "packages_problem": packages_problem,
        "timestamp": clock().isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m aid.freeze", description="Write the freeze record of the aid.")
    parser.add_argument("--settings", type=Path, required=True, help="the settings file of the frozen aid")
    parser.add_argument("--out", type=Path, help="the record (default freeze_<date>.json in the current folder)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.settings)
    except (OSError, ValueError) as error:          # missing, unreadable, not JSON or not valid settings (a ValidationError)
        parser.error(f"the settings file {args.settings} cannot be read: {error}")
    moment = now()
    record = freeze_record(args.settings, settings, REPO_ROOT, lambda: moment)
    out = args.out or Path(f"freeze_{moment.date().isoformat()}.json")
    partial = out.with_name(out.name + ".partial")
    partial.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, out)
    print(f"Freeze record written to {out}, commit {record['git_commit']}")
    return 0


def _git(repo_root: Path, *args: str) -> tuple[str | None, str | None]:
    """The output of a git command in `repo_root`, stripped, and None; or None and why there is none (git is not available, does
    not answer in time, or fails, as outside a repository)."""
    try:
        done = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"git {args[0]} did not answer within {TIMEOUT} seconds"
    except OSError as error:
        return None, f"git is not available: {error}"
    if done.returncode != 0:
        return None, f"git {args[0]} failed: {done.stderr.strip()}"
    return done.stdout.strip(), None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt(path: Path, repo_root: Path) -> dict:
    first = path.read_text(encoding="utf-8").partition("\n")[0].strip()
    found = _PROMPT_VERSION.fullmatch(first)
    return {"path": path.relative_to(repo_root).as_posix(), "marker": first, "version": found[1] if found else None,
            "sha256": _sha256(path)}


def _packages() -> tuple[str, list[str], str | None]:
    """The installed packages: the lines of `pip freeze`, or the distributions importlib.metadata lists when pip is not available
    or does not answer in time; with where they come from and why pip was not used (None when it was)."""
    try:
        done = subprocess.run(PIP_FREEZE, capture_output=True, text=True, timeout=TIMEOUT)
        problem = None if done.returncode == 0 else f"pip freeze failed: {done.stderr.strip()}"
    except subprocess.TimeoutExpired:
        problem = f"pip freeze did not answer within {TIMEOUT} seconds"
    except OSError as error:
        problem = f"pip is not available: {error}"
    if problem is None:
        return "pip freeze", [line for line in done.stdout.splitlines() if line.strip()], None
    found = {f"{dist.metadata['Name']}=={dist.version}" for dist in metadata.distributions() if dist.metadata["Name"]}
    return "importlib.metadata", sorted(found, key=str.lower), problem


if __name__ == "__main__":
    raise SystemExit(main())
