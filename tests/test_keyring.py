from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from techvault.errors import ErrorCode, KeyringUnavailableError
from techvault.security.token_store import (
    ACCOUNT_NAME,
    SERVICE_NAME,
    delete_token,
    has_token,
    load_token,
    save_token,
)


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.passwords[(service, username)]
        except KeyError as exc:
            raise PasswordDeleteError("missing") from exc


class FailingKeyring(KeyringBackend):
    priority = 1

    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("keyring down")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("keyring down")

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("keyring down")


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


def test_save_and_load_token(memory_keyring: MemoryKeyring) -> None:
    save_token("ghp_UNITTESTTOKEN")
    assert memory_keyring.passwords[(SERVICE_NAME, ACCOUNT_NAME)] == "ghp_UNITTESTTOKEN"
    assert SERVICE_NAME == "TechVault"
    assert ACCOUNT_NAME == "github-pat"
    assert load_token() == "ghp_UNITTESTTOKEN"


def test_delete_token(memory_keyring: MemoryKeyring) -> None:
    save_token("ghp_UNITTESTTOKEN")
    delete_token()
    assert load_token() is None
    delete_token()
    assert (SERVICE_NAME, ACCOUNT_NAME) not in memory_keyring.passwords


def test_has_token(memory_keyring: MemoryKeyring) -> None:
    assert has_token() is False
    save_token("ghp_UNITTESTTOKEN")
    assert has_token() is True
    delete_token()
    assert has_token() is False


def test_keyring_failure_does_not_write_plaintext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(FailingKeyring())
    monkeypatch.chdir(tmp_path)
    token = "ghp_SHOULD_NOT_BE_STORED"
    try:
        with pytest.raises(KeyringUnavailableError) as caught:
            save_token(token)
        assert caught.value.code == ErrorCode.KEYRING_UNAVAILABLE
        with pytest.raises(KeyringUnavailableError):
            load_token()
        with pytest.raises(KeyringUnavailableError):
            has_token()
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert token not in path.read_text(encoding="utf-8", errors="ignore")
    finally:
        keyring.set_keyring(previous)
