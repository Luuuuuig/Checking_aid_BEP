"""Tests of the protected store of secrets on this PC (aid/connections/secrets.py). The conftest makes msal-extensions' encrypted
persistence unavailable, so that a store made here keeps its secrets in a plain owner-only file under tmp_path, unless a test
gives it a persistence of its own."""
import os
import stat
import sys

import msal
import msal_extensions
import pytest
from msal_extensions.persistence import PersistenceNotFound

from aid.connections.secrets import LazySecretStore, SecretStore, StoreRefused, default_secrets_dir
from aid.tests.conftest import REPO_ROOT
from aid.tests.fakes import sign_in_event

SECRET = "made-up-secret-value-0001"
PLAIN_ONLY = pytest.mark.skipif(os.name == "nt", reason="on Windows the store always encrypts with DPAPI: no plain file to look into")


def test_the_store_refuses_a_folder_inside_a_git_work_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(StoreRefused, match="git work tree"):
        SecretStore(repo / "aid" / "secrets")
    assert not (repo / "aid").exists()                       # refused before anything is made
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/w\n", encoding="utf-8")    # a worktree's .git is a file
    with pytest.raises(ValueError, match="git work tree"):
        SecretStore(worktree / "secrets")
    if (REPO_ROOT / ".git").exists():                         # a checkout of the repository (not a copy without .git)
        with pytest.raises(ValueError, match="git work tree"):
            SecretStore(REPO_ROOT / "aid" / "secrets")          # the repository of the aid itself
        assert not (REPO_ROOT / "aid" / "secrets").exists()


@PLAIN_ONLY
def test_a_secret_is_kept_in_a_file_that_only_its_owner_can_read(tmp_path, capsys):
    store = SecretStore(tmp_path / "secrets")
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "not encrypted" in err and "owner" in err      # one line says that the store is a plain file
    store.set("exact_token", SECRET)
    assert store.get("exact_token") == SECRET and SecretStore(tmp_path / "secrets").get("exact_token") == SECRET
    holding = [path for path in (tmp_path / "secrets").iterdir() if path.is_file() and SECRET.encode() in path.read_bytes()]
    assert holding
    if os.name == "posix":
        assert {stat.S_IMODE(path.stat().st_mode) for path in holding} == {0o600}
        assert stat.S_IMODE((tmp_path / "secrets").stat().st_mode) == 0o700
    assert SECRET not in repr(store) and SECRET not in str(store) and SECRET not in capsys.readouterr().out
    store.delete("exact_token")
    assert store.get("exact_token") is None
    store.delete("exact_token")                              # deleting a secret that is not there is no error
    assert store.get("never_set") is None
    if os.name == "posix":                                   # a file loosened by hand is made owner-only again at the next save
        [file] = [path for path in holding if path.name == "secrets.bin"]
        file.chmod(0o644)
        store.set("again", SECRET)
        assert stat.S_IMODE(file.stat().st_mode) == 0o600


@PLAIN_ONLY
def test_a_damaged_store_names_its_file_and_never_its_content(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    store.set("a", "b")
    [file] = [path for path in (tmp_path / "secrets").iterdir() if path.is_file() and b'"a"' in path.read_bytes()]
    file.write_text(f"not json {SECRET}", encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        store.get("a")
    assert file.name in str(raised.value) and SECRET not in str(raised.value)


def test_the_token_cache_is_shared_by_the_stores_of_one_folder(tmp_path):
    """The review page and the runner each make a store on the same folder: a sign-in on the page is in the runner's cache."""
    page, runner = SecretStore(tmp_path / "secrets"), SecretStore(tmp_path / "secrets")
    page.token_cache().add(sign_in_event("buyer@example.com"))
    assert [account["username"] for account in runner.token_cache().search(msal.TokenCache.CredentialType.ACCOUNT)] == ["buyer@example.com"]
    assert page.token_cache() is page.token_cache()          # one cache per store
    if os.name == "posix":
        holding = [path for path in (tmp_path / "secrets").iterdir() if path.is_file() and b"made-up-refresh" in path.read_bytes()]
        assert holding and {stat.S_IMODE(path.stat().st_mode) for path in holding} == {0o600}


class FakePersistence:
    """An encrypted persistence of msal-extensions as the store uses it, kept in memory: what it saves is in no file."""
    is_encrypted = True
    made: list = []

    def __init__(self, location):
        self.location, self.content, self.saved_at = str(location), None, 0.0
        FakePersistence.made.append(self)

    def save(self, content):
        self.content, self.saved_at = content, self.saved_at + 1

    def load(self):
        if self.content is None:
            raise PersistenceNotFound(message="nothing saved yet", location=self.location)
        return self.content

    def time_last_modified(self):
        if self.content is None:
            raise PersistenceNotFound(message="nothing saved yet", location=self.location)
        return self.saved_at

    def get_location(self):
        return self.location


def test_on_windows_the_store_encrypts_with_dpapi(tmp_path, monkeypatch, capsys):
    FakePersistence.made = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(msal_extensions, "FilePersistenceWithDataProtection", FakePersistence)
    store = SecretStore(tmp_path / "secrets")
    store.set("exact_token", SECRET)
    assert store.get("exact_token") == SECRET and FakePersistence.made and capsys.readouterr().err == ""
    assert not [path for path in (tmp_path / "secrets").iterdir() if path.is_file() and SECRET.encode() in path.read_bytes()]


def test_elsewhere_the_encrypted_persistence_is_used_when_the_system_has_one(tmp_path, monkeypatch, capsys):
    FakePersistence.made = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(msal_extensions, "build_encrypted_persistence", FakePersistence)
    store = SecretStore(tmp_path / "secrets")
    store.set("exact_token", SECRET)
    assert store.get("exact_token") == SECRET and FakePersistence.made and capsys.readouterr().err == ""


def test_the_default_folder_is_local_app_data_on_windows_and_the_config_folder_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert default_secrets_dir() == tmp_path / "Local" / "Inkoopassistent"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))           # what Path.home() reads on Windows
    assert default_secrets_dir() == tmp_path / "home" / ".config" / "inkoopassistent"


@PLAIN_ONLY
def test_a_folder_keeps_the_kind_of_store_it_was_made_with(tmp_path, monkeypatch, capsys):
    """The page and the runner may start in different sessions (a desktop and a scheduled task): a folder made with a plain file
    stays plain, so that both read one cache, and a folder made with the system's keyring is refused by a process without one,
    with a message, instead of a second, empty cache."""
    plain = SecretStore(tmp_path / "plain")
    plain.set("exact_token", SECRET)
    FakePersistence.made = []
    monkeypatch.setattr(msal_extensions, "build_encrypted_persistence", FakePersistence)
    assert SecretStore(tmp_path / "plain").get("exact_token") == SECRET and FakePersistence.made == []
    keyring = tmp_path / "keyring"
    SecretStore(keyring).set("exact_token", SECRET)                     # made where the keyring answers
    def no_keyring(location):                                           # a session without the keyring
        raise RuntimeError("no keyring here")

    monkeypatch.setattr(msal_extensions, "build_encrypted_persistence", no_keyring)
    with pytest.raises(StoreRefused) as refused:
        SecretStore(keyring)
    assert "fakepersistence" in str(refused.value) and "same user" in str(refused.value) and SECRET not in str(refused.value)


def test_the_lazy_store_is_made_at_its_first_use_and_refused_then(tmp_path):
    """The runner's store: making the Deps never refuses (the runner starts and handles its folder), and the store's refusal comes
    with the first use of the sign-in, where the mailbox stage logs it. A refusal is a ValueError too, as before."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    lazy = LazySecretStore(repo / "secrets")
    assert lazy.directory == (repo / "secrets").resolve() and not (repo / "secrets").exists() and "secrets" in repr(lazy)
    with pytest.raises(StoreRefused, match="git work tree"):
        lazy.token_cache()
    assert issubclass(StoreRefused, ValueError)
    store = LazySecretStore(tmp_path / "secrets")
    store.set("exact_token", SECRET)
    assert store.get("exact_token") == SECRET and SecretStore(tmp_path / "secrets").get("exact_token") == SECRET
    store.token_cache().add(sign_in_event("buyer@example.com"))
    assert [a["username"] for a in SecretStore(tmp_path / "secrets").token_cache().search(msal.TokenCache.CredentialType.ACCOUNT)] == \
        ["buyer@example.com"]
    store.delete("exact_token")
    assert store.get("exact_token") is None and SECRET not in repr(store)
