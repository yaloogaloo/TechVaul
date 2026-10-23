"""User-facing video archive errors."""


class VideoArchiveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
