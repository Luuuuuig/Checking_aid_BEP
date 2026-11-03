"""Tests of the freeze record (aid/freeze.py). git runs in a temporary repository of the test's own and pip runs locally: no test
calls the network."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aid import freeze
from aid.pipeline import Deps
from aid.routing import RULE_SETTINGS
from aid.tests.conftest import REPO_ROOT, SETTINGS

CLOCK = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
PROMPTS = Path("aid") / "readers" / "prompts"
NO_PIP = [sys.executable, "-m", "no_such_module_for_the_freeze"]


def git(folder: Path, *args: str) -> str:
    """A git command in `folder` with a made-up author and no signing, whatever the user's git configuration says."""
    command = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, cwd=folder, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A git repository of its own with the aid's prompt files in one commit."""
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / PROMPTS, root / PROMPTS)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "prompts")
    return root


@pytest.fixture
def settings_file(tmp_path):
    """A settings file of made-up values, among them the made-up test limit of the test settings, outside the repository."""
    path = tmp_path / "settings.json"
    path.write_text(SETTINGS.model_dump_json(indent=2), encoding="utf-8")
    return path


def record_of(settings_file: Path, root: Path) -> dict:
    return freeze.freeze_record(settings_file, freeze.load_settings(settings_file), root, lambda: CLOCK)


def holds_the_limit(line: str) -> bool:
    """Whether a line of the record holds the amount of the authorization limit, as a number of its own (not inside a hash)."""
    amount = re.escape(format(SETTINGS.authorization_limit.normalize(), "f"))
    return re.search(rf"(?<![\w.]){amount}(\.0+)?(?![\w.])", line) is not None


def test_the_freeze_record_in_a_git_checkout(settings_file, repo, monkeypatch):
    monkeypatch.setattr(freeze, "PIP_FREEZE", NO_PIP)              # pip is the subject of its own test
    record = record_of(settings_file, repo)
    assert record["git_commit"] == git(repo, "rev-parse", "HEAD") and record["git_problem"] is None
    assert record["working_tree_clean"] is True
    assert record["settings_sha256"] == hashlib.sha256(settings_file.read_bytes()).hexdigest()
    settings = freeze.load_settings(settings_file)
    assert record["settings"]["authorization_limit"] == "set" and record["settings"]["model"] == settings.model == record["model"]
    assert record["settings"]["price_pct"] == str(settings.price_pct)
    prompts = {prompt["path"]: prompt for prompt in record["prompts"]}
    assert sorted(prompts) == ["aid/readers/prompts/decide.md", "aid/readers/prompts/read_document.md"]
    assert prompts["aid/readers/prompts/read_document.md"] == {
        "path": "aid/readers/prompts/read_document.md", "marker": "<!-- read_document v1 -->", "version": "v1",
        "sha256": hashlib.sha256((REPO_ROOT / PROMPTS / "read_document.md").read_bytes()).hexdigest()}
    assert sorted(record["rules"]) == [f"R{i:02d}" for i in range(1, 12)]
    assert record["rules"]["R04"] == {"settings": {"price_pct": str(settings.price_pct), "price_abs": str(settings.price_abs)}}
    assert record["rules"]["R10"] == {"settings": {"authorization_limit": "set"}} and record["rules"]["R01"] == {"settings": {}}
    assert all(list(record["rules"][rule]["settings"]) == list(RULE_SETTINGS[rule]) for rule in record["rules"])
    assert record["aid_version"] == Deps.model_fields["aid_version"].default
    assert record["python"] == sys.version.split()[0] and record["timestamp"] == CLOCK.isoformat()
    (repo / "untracked.txt").write_text("a change", encoding="utf-8")
    assert record_of(settings_file, repo)["working_tree_clean"] is False


def test_outside_a_repository_the_commit_is_unknown(settings_file, tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "PIP_FREEZE", NO_PIP)
    plain = tmp_path / "plain"
    plain.mkdir()
    record = record_of(settings_file, plain)
    assert record["git_commit"] == "unknown" and record["working_tree_clean"] is None
    assert record["git_problem"].startswith("git rev-parse failed") and record["prompts"] == []


def test_the_packages_come_from_pip_freeze(settings_file, tmp_path):
    record = record_of(settings_file, tmp_path)
    assert record["packages_source"] == "pip freeze" and record["packages_problem"] is None
    assert any(line.lower().startswith("pydantic==") for line in record["packages"])


def test_without_pip_the_packages_are_the_installed_distributions(settings_file, tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "PIP_FREEZE", NO_PIP)
    record = record_of(settings_file, tmp_path)
    assert record["packages_source"] == "importlib.metadata" and record["packages_problem"].startswith("pip freeze failed")
    assert any(line.lower().startswith("pydantic==") for line in record["packages"])


def test_git_and_pip_that_do_not_answer_in_time(settings_file, repo, monkeypatch):
    """A timeout counts as an unknown commit for git and as the importlib.metadata fallback for pip, and the record says so."""
    def hanging(command, **kwargs):
        assert kwargs["timeout"] == freeze.TIMEOUT == 60
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(freeze.subprocess, "run", hanging)
    record = record_of(settings_file, repo)
    assert record["git_commit"] == "unknown" and record["git_problem"] == "git rev-parse did not answer within 60 seconds"
    assert record["packages_source"] == "importlib.metadata"
    assert record["packages_problem"] == "pip freeze did not answer within 60 seconds"


def test_an_unset_limit_is_recorded_as_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "PIP_FREEZE", NO_PIP)
    path = tmp_path / "settings.json"
    path.write_text(SETTINGS.model_copy(update={"authorization_limit": None}).model_dump_json(), encoding="utf-8")
    record = record_of(path, tmp_path)
    assert record["settings"]["authorization_limit"] == "unset" == record["rules"]["R10"]["settings"]["authorization_limit"]


# --- the command ---

def test_the_command_writes_the_record_without_the_amount(settings_file, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(freeze, "REPO_ROOT", repo)
    out = tmp_path / "freeze.json"
    assert freeze.main(["--settings", str(settings_file), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    record = json.loads(text)
    assert text == json.dumps(record, indent=2, sort_keys=True) + "\n"
    assert settings_file.read_text(encoding="utf-8").count(format(SETTINGS.authorization_limit, "f")) == 1   # the file holds it
    assert not [line for line in text.splitlines() if holds_the_limit(line)]
    assert record["settings"]["authorization_limit"] == "set" and record["git_commit"] == git(repo, "rev-parse", "HEAD")
    printed = capsys.readouterr().out
    assert str(out) in printed and record["git_commit"] in printed
    assert not list(tmp_path.glob("*.partial"))


def test_the_record_is_named_after_its_date_by_default(settings_file, tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "PIP_FREEZE", NO_PIP)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(freeze, "now", lambda: CLOCK)
    assert freeze.main(["--settings", str(settings_file)]) == 0
    assert json.loads((tmp_path / "freeze_2026-11-27.json").read_text(encoding="utf-8"))["timestamp"] == CLOCK.isoformat()


@pytest.mark.parametrize("content", [None, "{not json", '{"price_pct": "2.0"}'])
def test_a_settings_file_that_is_missing_or_does_not_parse_is_a_command_error(tmp_path, capsys, content):
    path = tmp_path / "settings.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        freeze.main(["--settings", str(path), "--out", str(tmp_path / "freeze.json")])
    assert stopped.value.code == 2 and f"the settings file {path} cannot be read" in capsys.readouterr().err
    assert not list(tmp_path.glob("freeze.json*"))
