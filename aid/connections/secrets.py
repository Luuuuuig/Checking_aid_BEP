"""The protected store of the secrets on this PC: the token cache of the Microsoft sign-in, which the review page and the runner
share, and other secrets by name.

SecretStore(directory) keeps two files in the directory: token_cache.bin, the sign-in's token cache (msal-extensions'
PersistedTokenCache, with a lock file beside it while it is written), and secrets.bin, the named secrets as one JSON object. On
Windows both are encrypted with DPAPI through msal-extensions (FilePersistenceWithDataProtection), so that only this Windows user on
this PC can read them. Elsewhere the store takes msal-extensions' encrypted persistence (the keychain on macOS, libsecret on Linux)
when the system has one, and otherwise keeps the two files as plain files that only their owner can read and write (permissions
0600), in a folder that only the owner can open (0700); one line to stderr says so, once per folder in a process. The kind of store
a folder was made with is written in its file "backend" ("dpapi", "keychain", "libsecret" or "plain"), and every later store on
the folder takes that kind: a folder made plain stays plain, and a process that cannot use the kind recorded (a runner started
without the desktop session whose keyring the page used) is refused with StoreRefused, a ValueError that says so, instead of
reading a second, empty cache. On Windows the cache is encrypted for one Windows user: the review page and the runner must run as
that same user.

The store refuses a directory that lies inside a git work tree (the directory, or a folder above it, holds .git), so that a token
can never be committed, with StoreRefused too. work_tree_refusal(directory) gives that refusal without making anything, so that the
review page can say why a sign-in cannot be made before the buyer tries one. A refusal carries the store's folder and the folder
that holds .git, or the kind of store recorded, by which the review page names them on its settings tab; the mailer of the work
mailbox names neither (aid/messaging/microsoft_mailer.py). Nothing in the store is ever printed, logged, or
written to the settings file, a case folder or the audit: its repr names the directory only, and an error names the file and the
problem, never a value.

LazySecretStore(directory) is the same store made at its first use, which the runner takes: making the runner's Deps then never
refuses, so that the runner starts and handles its folder, and a refusal comes with the first use of the sign-in, where the
mailbox stage of the runner logs it.

default_secrets_dir() is the folder that the runner and the review page use: %LOCALAPPDATA%\\Inkoopassistent on Windows,
~/.config/inkoopassistent elsewhere. The settings name no folder for it.
"""
import json
import os
import sys
from pathlib import Path

import msal
import msal_extensions
from msal_extensions.persistence import PersistenceNotFound

TOKEN_CACHE = "token_cache.bin"
SECRETS = "secrets.bin"
BACKEND = "backend"                               # the kind of store the folder was made with
PLAIN = "plain"
_NAMED: set[Path] = set()                         # the folders of plain files that this process has named on stderr


def default_secrets_dir() -> Path:
    """%LOCALAPPDATA%\\Inkoopassistent on Windows (under the home folder's AppData\\Local when the variable is not set), and
    ~/.config/inkoopassistent elsewhere."""
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "Inkoopassistent"
    return Path.home() / ".config" / "inkoopassistent"


class StoreRefused(ValueError):
    """A store this process may not use: a folder inside a git work tree, or one made with a kind of store this process has not.
    Beside its message it carries what the review page names to say why: `directory`, the folder of the store; `work_tree`, the
    folder at or above it that holds .git, for a folder inside a git work tree; and `recorded`, the kind of store the folder was made
    with, for a folder of another kind. A refusal made with a message alone carries None for each."""

    def __init__(self, message: str, *, directory: Path | None = None, work_tree: Path | None = None, recorded: str | None = None):
        super().__init__(message)
        self.directory, self.work_tree, self.recorded = directory, work_tree, recorded


class SecretStore:
    """A small store of secrets on this PC, encrypted where the system allows it (see the module docstring)."""

    def __init__(self, directory: Path):
        directory = Path(directory).expanduser().resolve()
        if (refused := work_tree_refusal(directory)) is not None:
            raise refused
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(directory, 0o700)
        self.directory = directory
        recorded = (directory / BACKEND).read_text(encoding="utf-8").strip() if (directory / BACKEND).exists() else None
        self._secrets, kind = _persistence(directory / SECRETS, plain=recorded == PLAIN)
        self._cached, _ = _persistence(directory / TOKEN_CACHE, plain=recorded == PLAIN)
        if recorded is None:
            (directory / BACKEND).write_text(kind + "\n", encoding="utf-8")
        elif recorded != kind:
            raise StoreRefused(f"the secrets store in {directory} was made with the store {recorded!r}, which this process cannot use (it "
                               f"has {kind!r}): start the review page and the runner as the same user, in the same kind of session",
                               directory=directory, recorded=recorded)
        self._cache: msal.TokenCache | None = None
        if kind == PLAIN and directory not in _NAMED:
            _NAMED.add(directory)
            print(f"aid: the secrets store in {directory} is not encrypted, as this system offers no encrypted store; its files can be "
                  "read by their owner only", file=sys.stderr)

    def __repr__(self) -> str:
        return f"SecretStore({str(self.directory)!r})"

    def token_cache(self) -> msal.TokenCache:
        """The sign-in's persisted token cache: one per store, read again from its file whenever another process changed it."""
        if self._cache is None:
            self._cache = msal_extensions.PersistedTokenCache(self._cached)
        return self._cache

    def get(self, name: str) -> str | None:
        """The secret of that name, or None."""
        return self._load().get(name)

    def set(self, name: str, value: str) -> None:
        with self._lock():
            self._secrets.save(json.dumps(self._load() | {name: value}))

    def delete(self, name: str) -> None:
        """Remove the secret of that name; a name the store does not hold is no error."""
        with self._lock():
            secrets = self._load()
            if name in secrets:
                del secrets[name]
                self._secrets.save(json.dumps(secrets))

    def _lock(self) -> msal_extensions.CrossPlatLock:
        return msal_extensions.CrossPlatLock(str(self.directory / f"{SECRETS}.lockfile"))

    def _load(self) -> dict[str, str]:
        try:
            text = self._secrets.load()
        except PersistenceNotFound:                  # nothing saved yet
            return {}
        try:
            secrets = json.loads(text)
        except ValueError:
            secrets = None
        if not isinstance(secrets, dict):
            raise ValueError(f"{SECRETS} of the secrets store in {self.directory} cannot be read: it holds no JSON object")
        return secrets


class LazySecretStore:
    """A SecretStore made at its first use (see the module docstring); StoreRefused then, when it is refused."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).expanduser().resolve()
        self._store: SecretStore | None = None

    def __repr__(self) -> str:
        return f"LazySecretStore({str(self.directory)!r})"

    def token_cache(self) -> msal.TokenCache:
        return self._made().token_cache()

    def get(self, name: str) -> str | None:
        return self._made().get(name)

    def set(self, name: str, value: str) -> None:
        self._made().set(name, value)

    def delete(self, name: str) -> None:
        self._made().delete(name)

    def _made(self) -> SecretStore:
        if self._store is None:
            self._store = SecretStore(self.directory)
        return self._store


class _OwnerOnlyFile(msal_extensions.FilePersistence):
    """msal-extensions' plain file, which it creates with permissions 0600, kept at 0600 when the file was there before."""

    def save(self, content: str) -> None:
        if os.path.exists(self.get_location()):
            os.chmod(self.get_location(), 0o600)
        super().save(content)


_KINDS = {"FilePersistenceWithDataProtection": "dpapi", "KeychainPersistence": "keychain", "LibsecretPersistence": "libsecret"}


def _persistence(location: Path, plain: bool = False) -> tuple[object, str]:
    """The persistence of a file of the store and its kind (see the module docstring): DPAPI on Windows; elsewhere the system's
    encrypted store when msal-extensions can use it, else (and when `plain` is asked) a plain file only its owner can read."""
    if sys.platform.startswith("win"):
        persistence = msal_extensions.FilePersistenceWithDataProtection(str(location))
    elif plain:
        return _OwnerOnlyFile(str(location)), PLAIN
    else:
        try:
            persistence = msal_extensions.build_encrypted_persistence(str(location))
        except Exception:                            # no keychain or libsecret here: its library is missing or its trial run fails
            return _OwnerOnlyFile(str(location)), PLAIN
    name = type(persistence).__name__
    return persistence, _KINDS.get(name, name.lower())


def work_tree_refusal(directory: Path) -> StoreRefused | None:
    """The refusal of a store in the directory because it lies inside a git work tree (see the module docstring), as SecretStore
    raises it, or None for a directory outside every work tree. The directory is taken as the store takes it (from ~, its links
    resolved) and only looked at: no folder and no file is made."""
    directory = Path(directory).expanduser().resolve()
    if (work_tree := _work_tree(directory)) is None:
        return None
    return StoreRefused(f"the secrets store may not lie inside a git work tree ({work_tree} holds .git), so that no token can be "
                        "committed: choose a folder outside the repository", directory=directory, work_tree=work_tree)


def _work_tree(directory: Path) -> Path | None:
    """The folder at or above the directory that holds .git (a folder, or the file of a git worktree), or None."""
    for folder in (directory, *directory.parents):
        if (folder / ".git").exists():
            return folder
    return None
