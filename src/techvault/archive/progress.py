"""Byte progress for one archive job. The UI only displays these events."""

from __future__ import annotations

import time
from dataclasses import dataclass

THROTTLE_SECONDS = 0.2


@dataclass(frozen=True)
class ProgressEvent:
    job_id: int | None
    file_id: int | None
    current_file: str
    downloaded_bytes: int
    expected_bytes: int | None
    overall_downloaded_bytes: int
    overall_expected_bytes: int | None
    percentage: int | None
    completed_files: int = 0
    total_files: int = 0
    phase: str = "downloading"


class TransferPlan:
    """Combine per-file byte counts into one job percentage."""

    def __init__(self, expected_sizes: list[int | None]) -> None:
        self._queue = list(expected_sizes)
        self.completed_bytes = 0
        self.completed_expected = 0
        self.current_downloaded = 0
        self.current_expected: int | None = None
        self._active = False
        self._last = 0.0

    def begin(self) -> None:
        size = self._queue.pop(0) if self._queue else None
        self.current_downloaded = 0
        self.current_expected = size
        self._active = True

    def observe(
        self,
        downloaded: int,
        expected: int | None,
        *,
        force: bool = False,
        job_id: int | None = None,
        file_id: int | None = None,
        current_file: str = "",
        completed_files: int = 0,
        total_files: int = 0,
    ) -> ProgressEvent | None:
        now = time.monotonic()
        if not force and now - self._last < THROTTLE_SECONDS:
            return None
        self._last = now
        self.current_downloaded = downloaded
        if expected is not None:
            self.current_expected = expected
        return self.snapshot(
            job_id=job_id,
            file_id=file_id,
            current_file=current_file,
            completed_files=completed_files,
            total_files=total_files,
        )

    def finish(self, actual: int) -> None:
        self.completed_bytes += actual
        if self.current_expected is not None:
            self.completed_expected += self.current_expected
        else:
            self.completed_expected += actual
        self.current_downloaded = 0
        self.current_expected = None
        self._active = False

    def snapshot(
        self,
        *,
        job_id: int | None = None,
        file_id: int | None = None,
        current_file: str = "",
        completed_files: int = 0,
        total_files: int = 0,
    ) -> ProgressEvent:
        downloaded = self.completed_bytes + self.current_downloaded
        unknown = any(item is None for item in self._queue) or (
            self._active and self.current_expected is None
        )
        if unknown:
            expected: int | None = None
            percentage = None
        else:
            current_expected = 0 if self.current_expected is None else self.current_expected
            expected = self.completed_expected + current_expected + sum(self._queue)
            percentage = 0 if expected <= 0 else min(100, downloaded * 100 // expected)
        return ProgressEvent(
            job_id=job_id,
            file_id=file_id,
            current_file=current_file,
            downloaded_bytes=self.current_downloaded,
            expected_bytes=self.current_expected,
            overall_downloaded_bytes=downloaded,
            overall_expected_bytes=expected,
            percentage=percentage,
            completed_files=completed_files,
            total_files=total_files,
        )


def format_bytes(value: int) -> str:
    amount = float(value)
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if amount < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(amount)} B"
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"
