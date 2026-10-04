"""Stable error codes for the phase-1 foundation."""


class ErrorCode:
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    AUTH = "AUTH"
    AUTH_INVALID = "AUTH_INVALID"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    REPO_NOT_VISIBLE = "REPO_NOT_VISIBLE"
    RATE_LIMIT = "RATE_LIMIT"
    API_ERROR = "API_ERROR"
    INVALID_URL = "INVALID_URL"
    UNSAFE_PATH = "UNSAFE_PATH"
    DATABASE = "DATABASE"
    KEYRING_UNAVAILABLE = "KEYRING_UNAVAILABLE"


class TechVaultError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InvalidUrlError(TechVaultError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_URL, message)


class UnsafePathError(TechVaultError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.UNSAFE_PATH, message)


class DatabaseError(TechVaultError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.DATABASE, message)


class KeyringUnavailableError(TechVaultError):
    def __init__(self, message: str = "Keyring is unavailable") -> None:
        super().__init__(ErrorCode.KEYRING_UNAVAILABLE, message)


class GitHubHTTPError(TechVaultError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        rate_limit: object | None = None,
    ) -> None:
        super().__init__(code, message)
        self.status_code = status_code
        self.rate_limit = rate_limit
