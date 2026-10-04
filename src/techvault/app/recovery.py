"""Close archive jobs left behind when the process exited."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

INTERRUPTED = "INTERRUPTED_BY_PROCESS_EXIT"
INTERRUPTED_MESSAGE = "归档因进程退出而中断。"


def recover_interrupted_archives(connection: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc).isoformat()
    jobs = connection.execute(
        "SELECT id FROM jobs WHERE status = 'ARCHIVING'"
    ).fetchall()
    if not jobs:
        return 0
    connection.execute("BEGIN")
    connection.execute(
        """
        UPDATE source_snapshots
        SET state = 'FAILED', failure_code = ?, failure_message = ?, updated_at = ?
        WHERE state IN ('PENDING', 'DOWNLOADING', 'VERIFYING')
        """,
        (INTERRUPTED, INTERRUPTED_MESSAGE, now),
    )
    connection.execute(
        """
        UPDATE release_files
        SET state = 'FAILED', failure_code = ?, failure_message = ?, updated_at = ?
        WHERE state IN ('PENDING', 'DOWNLOADING', 'VERIFYING')
        """,
        (INTERRUPTED, INTERRUPTED_MESSAGE, now),
    )
    connection.execute(
        """
        UPDATE release_attempts
        SET status = 'FAILED', failure_code = ?, failure_message = ?, finished_at = ?
        WHERE status = 'ARCHIVING'
        """,
        (INTERRUPTED, INTERRUPTED_MESSAGE, now),
    )
    connection.execute(
        """
        UPDATE jobs
        SET status = 'FAILED', failure_code = ?, failure_message = ?, finished_at = ?
        WHERE status = 'ARCHIVING'
        """,
        (INTERRUPTED, INTERRUPTED_MESSAGE, now),
    )
    connection.execute("COMMIT")
    return len(jobs)
