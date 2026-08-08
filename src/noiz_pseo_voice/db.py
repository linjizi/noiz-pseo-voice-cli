"""Optional read-only (and gated write) access to the voices DB."""

from __future__ import annotations

from typing import Any, Optional


class DbError(RuntimeError):
    pass


def _connect(dsn: str):
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover
        raise DbError(
            "psycopg2 is required for DB-backed commands; install with "
            "`pip install noiz-pseo-voice-cli[db]`"
        ) from exc
    try:
        return psycopg2.connect(dsn, connect_timeout=10)
    except Exception as exc:
        raise DbError(f"DB connection failed: {exc}") from exc


def queue_counts(dsn: str) -> dict[str, Any]:
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, count(*) FROM voices_pipeline_queue GROUP BY status"
            )
            counts = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute(
                "SELECT last_voice_id, updated_at FROM pipeline_state WHERE id = 1"
            )
            cursor_row = cur.fetchone()
        return {
            "counts": counts,
            "cursor": int(cursor_row[0]) if cursor_row else None,
            "cursor_updated_at": (
                cursor_row[1].isoformat() if cursor_row and cursor_row[1] else None
            ),
        }
    finally:
        conn.close()


def queue_voice_exists(dsn: str, voice_id: str) -> bool:
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM voices_pipeline_queue WHERE voice_id = %s",
                (voice_id,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def enqueue_voice(dsn: str, voice_id: str) -> str:
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO voices_pipeline_queue (voice_id) VALUES (%s) "
                "ON CONFLICT (voice_id) DO NOTHING",
                (voice_id,),
            )
            result = "enqueued" if cur.rowcount == 1 else "skipped_dup"
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        raise DbError(f"enqueue failed: {exc}") from exc
    finally:
        conn.close()

