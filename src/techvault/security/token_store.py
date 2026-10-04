"""GitHub token storage. Tokens stay in the OS keyring."""

from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

from techvault.errors import KeyringUnavailableError

SERVICE_NAME = "TechVault"
ACCOUNT_NAME = "github-pat"


def save_token(token: str) -> None:
    if not isinstance(token, str) or token == "":
        raise ValueError("Token must be a non-empty string")
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, token)
    except Exception as exc:
        raise KeyringUnavailableError("Keyring is unavailable") from exc


def load_token() -> str | None:
    try:
        value = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception as exc:
        raise KeyringUnavailableError("Keyring is unavailable") from exc
    if value is None or value == "":
        return None
    return value


def delete_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except PasswordDeleteError:
        return
    except Exception as exc:
        raise KeyringUnavailableError("Keyring is unavailable") from exc


def has_token() -> bool:
    return load_token() is not None
