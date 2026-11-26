"""The freeze record: what the aid was when it was frozen, so that the held-out run can be shown to have run on that aid.

    python3 -m aid.freeze --settings settings.json [--out freeze_<date>.json]

freeze_record gathers:
- git_commit, the commit of HEAD (git rev-parse HEAD in the repository root), "unknown" when git is not available, does not answer
  within a minute or the folder is no repository, and git_problem says which (None when the commit is known); working_tree_clean,
  whether git status --porcelain shows nothing (None when it is not known);
- settings_sha256, the sha256 of the settings file, and settings, the settings themselves with the authorization limit shown as
  "set" or "unset" exactly as the audit shows it (aid/audit.py), and the addresses of the two exploration services of the settings
  tab, the endpoint of Azure OpenAI and the resource and the full address of Foundry (ADDRESSES), shown so as well; nothing else
  is hidden;
- prompts, each prompt file of aid/readers/prompts: its path, the version marker of its first line and its sha256;
- model, the model id of the settings;
- stand_in, the exploration reader or the stand-in that a run reads with, as build_deps of aid/runner.py chooses it: while the
  settings name a reader chosen on the settings tab ("azure_openai" or "foundry"), that reader, which build_deps takes whatever
  AID_STAND_IN holds, with the deployment of the settings (azure_openai_deployment or foundry_deployment); else, with reader "llm"
  and a value of AID_STAND_IN that the aid knows (stand_in.active), the reader that the environment chooses for a run that reads
  through AID_STAND_IN (aid/readers/stand_in.py): value, the value of AID_STAND_IN as the aid takes it, without the spaces around it
  and in lower case, and deployment, for azure_openai and foundry the name of the deployment in AZURE_OPENAI_DEPLOYMENT or
  AID_FOUNDRY_DEPLOYMENT, None when unset or blank, and None for anthropic, whose model is the one of the settings; else, for a run
  that reads with the parser or with Copilot itself, None for both. Never a key, an endpoint or a resource;
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
from collections.abc import Callable, Mapping
from datetime import datetime
from importlib import metadata
from pathlib import Path

from aid.audit import _settings_dump
from aid.pipeline import _PROMPT_VERSION, Deps
from aid.readers import exploration, stand_in
from aid.routing import FALLBACK_RULE, RULE_SETTINGS
from aid.settings import Settings, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = Path("aid") / "readers" / "prompts"
PIP_FREEZE = [sys.executable, "-m", "pip", "freeze"]
TIMEOUT = 60                                     # seconds a git or pip command may take


# The settings of the two exploration services that name their resource: shown as "set" or "unset", never as they are.
ADDRESSES = ("azure_openai_endpoint", "foundry_resource", "foundry_base_url")


def now() -> datetime:
    """The time of the freeze, from the clock; a function of its own so that a test can fix it."""
    return datetime.now().astimezone()


def freeze_record(settings_path: Path, settings: Settings, repo_root: Path, clock: Callable[[], datetime],
                  environ: Mapping[str, str] = os.environ) -> dict:
    """The freeze record (see the module docstring) of the settings read from `settings_path`, for the repository at `repo_root`,
    with the reader that the settings or the environment `environ` choose."""
    commit, git_problem = _git(repo_root, "rev-parse", "HEAD")
    status, _ = _git(repo_root, "status", "--porcelain") if commit is not None else (None, None)
    dumped = _settings_dump(settings)
    dumped |= {name: "set" if dumped.get(name) else "unset" for name in ADDRESSES}      # an address names the resource
    packages_source, packages, packages_problem = _packages()
    return {
        "git_commit": commit or "unknown",
        "git_problem": git_problem,
        "working_tree_clean": None if status is None else status == "",
        "settings_sha256": _sha256(Path(settings_path)),
        "settings": dumped,
        "prompts": [_prompt(path, repo_root) for path in sorted((repo_root / PROMPTS).glob("*.md"))],
        "model": settings.model,
        "stand_in": _stand_in(settings, environ),
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


def _stand_in(settings: Settings, environ: Mapping[str, str]) -> dict:
    """The reader a run reads with, exactly as build_deps of aid/runner.py chooses it (see the module docstring): the reader chosen
    on the settings tab with the deployment of the settings; else, with reader "llm" and a value of AID_STAND_IN that the aid knows
    (stand_in.active), that value as the aid takes it (stand_in.value), and for an exploration reader the name of its deployment in
    the environment, None when unset or blank; else (the parser, Copilot itself) None for both. Never a key or an address."""
    if settings.reader in exploration.SERVICES:           # build_deps takes it whatever AID_STAND_IN holds
        deployment = getattr(settings, exploration.SETTING_OF[exploration.VARIABLES[settings.reader][-1]]) or ""
        return {"value": settings.reader, "deployment": deployment.strip() or None}
    if settings.reader != "llm" or not stand_in.active(environ):      # build_deps reads with the parser, or with Copilot itself
        return {"value": None, "deployment": None}
    chosen = stand_in.value(environ)
    names = exploration.VARIABLES.get(chosen)
    deployment = environ.get(names[-1], "").strip() if names else ""
    return {"value": chosen, "deployment": deployment or None}


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
