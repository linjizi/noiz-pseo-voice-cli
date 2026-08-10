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


def voice_by_id(dsn: str, voice_id: str) -> Optional[dict[str, Any]]:
    """Read the voices row for a voice_id (A-tier ensure_voice)."""
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, voice_id, is_public, status, voice_type, delete_time, "
                "display_name, language, creation_mode "
                "FROM voices WHERE voice_id = %s",
                (voice_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "voice_id": row[1],
            "is_public": bool(row[2]),
            "status": row[3],
            "voice_type": row[4],
            "delete_time": row[5],
            "display_name": row[6],
            "language": row[7],
            "creation_mode": row[8],
        }
    finally:
        conn.close()


def search_voices(dsn: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find voice_ids by display_name/voice_id fuzzy match (cathan 2026-08-10:
    frontend users don't see voice ids; this is the discovery command feeding
    voice-to-page --voice-id)."""
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT voice_id, display_name, language, is_public, status, voice_type "
                "FROM voices "
                "WHERE (display_name ILIKE %s OR voice_id ILIKE %s) "
                "AND delete_time IS NULL "
                "ORDER BY id DESC LIMIT %s",
                (f"%{query}%", f"%{query}%", max(1, min(100, limit))),
            )
            rows = cur.fetchall()
        return [
            {
                "voice_id": row[0],
                "display_name": row[1],
                "language": row[2],
                "is_public": bool(row[3]),
                "status": row[4],
                "voice_type": row[5],
            }
            for row in rows
        ]
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
