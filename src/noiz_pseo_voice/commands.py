"""Command implementations. Every command returns a dict for --json output."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from . import __version__
from .audit import append, query
from .checks import run_checks
from .cms import CmsClient, CmsError
from .config import Config
from .db import DbError, enqueue_voice, queue_counts, queue_voice_exists


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_audit_entry(cfg: Config, command: str, args: list[str], ok: bool, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "t": _now_iso(),
        "caller": cfg.caller_id,
        "command": command,
        "args": args,
        "ok": ok,
        "version": __version__,
    }
    if extra:
        entry.update(extra)
    return entry


def doctor(cfg: Config, args: list[str]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if cfg.config_file:
        from pathlib import Path
        p = Path(cfg.config_file).expanduser()
        add("config_file", p.is_file(), str(p) if p.is_file() else "not found")
        add("config_file_perms", cfg.config_warning is None, cfg.config_warning or "mode ok")
    else:
        add("config_file", True, "none (env-only)")

    cms = CmsClient(cfg.cms_url, cfg.cms_api_key, cfg.cms_email, cfg.cms_password)
    try:
        cms.ping()
        add("cms_reachable", True, cfg.cms_url)
    except CmsError as exc:
        add("cms_reachable", False, str(exc))

    try:
        cms.verify_credentials()
        add("auth", True, "credentials ok")
    except CmsError as exc:
        add("auth", False, str(exc))

    if cfg.voices_db_url:
        try:
            queue_counts(cfg.voices_db_url)
            add("db_reachable", True, "queue tables readable")
        except DbError as exc:
            add("db_reachable", False, str(exc))
    else:
        add("db_reachable", True, "not configured (optional)")

    audit_dir = cfg.audit_log.parent
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        add("audit_writable", True, str(cfg.audit_log))
    except OSError as exc:
        add("audit_writable", False, str(exc))

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "cms_url": cfg.cms_url, "site_base": cfg.site_base}


def permissions(cfg: Config, args: list[str]) -> dict[str, Any]:
    """v0.4.0: no read/write tiers — any valid account may run all commands
    (voice-detail-pages chain only). This command verifies authentication."""
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key, cfg.cms_email, cfg.cms_password)
    try:
        cms.verify_credentials()
        auth = "ok"
    except CmsError as exc:
        auth = str(exc)
    return {
        "ok": auth == "ok",
        "auth": auth,
        "model": "CMS has no role tiers; any valid account may run all CLI commands (voice-detail-pages scope only)",
        "cms_url": cfg.cms_url,
    }


def status(cfg: Config, args: list[str]) -> dict[str, Any]:
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key)
    counts: dict[str, int] = {}
    page = 1
    limit = 100
    try:
        while True:
            docs, total = cms.list_records(limit=limit, depth=0, page=page)
            for doc in docs:
                key = str(doc.get("pipelineStatus") or "unknown")
                counts[key] = counts.get(key, 0) + 1
            if page * limit >= total or not docs:
                break
            page += 1
    except CmsError as exc:
        return {"ok": False, "error": str(exc)}

    out: dict[str, Any] = {
        "ok": True,
        "t": _now_iso(),
        "cms_url": cfg.cms_url,
        "total_records": sum(counts.values()),
        "by_pipeline_status": counts,
    }
    if cfg.voices_db_url:
        try:
            out["queue"] = queue_counts(cfg.voices_db_url)
        except DbError as exc:
            out["queue_error"] = str(exc)
    else:
        out["queue"] = {"note": "DB not configured; set NOIZ_VOICES_DB_URL"}
    return out


def voices_list(cfg: Config, args: list[str]) -> dict[str, Any]:
    status_filter = None
    locale_filter = None
    limit = 50
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--status" and i + 1 < len(args):
            status_filter = args[i + 1]
            i += 2
        elif a == "--locale" and i + 1 < len(args):
            locale_filter = args[i + 1]
            i += 2
        elif a == "--limit" and i + 1 < len(args):
            try:
                limit = max(1, min(500, int(args[i + 1])))
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    where: dict[str, Any] = {}
    if status_filter:
        where["pipelineStatus"] = {"equals": status_filter}
    if locale_filter:
        where["locale"] = {"equals": locale_filter}
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key)
    try:
        docs, total = cms.list_records(where, limit=min(limit, 500), depth=0)
    except CmsError as exc:
        return {"ok": False, "error": str(exc)}
    rows = [
        {
            "id": d.get("id"),
            "voiceId": d.get("voiceId"),
            "slug": d.get("canonicalSlug") or d.get("slug"),
            "status": d.get("pipelineStatus"),
            "updatedAt": d.get("updatedAt"),
        }
        for d in docs
    ]
    return {"ok": True, "total": total, "returned": len(rows), "voices": rows}


def voices_get(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "usage: voices get <id|voiceId|slug>"}
    key = args[0]
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key)
    try:
        record = cms.get_record(key, depth=2)
    except CmsError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "record": record}


def voices_create(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "usage: voices create <voiceId> [--name NAME] [--slug SLUG] [--status STATUS]"}
    voice_id = args[0]
    name = None
    slug = None
    status = "candidate_screening"
    i = 1
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--slug" and i + 1 < len(args):
            slug = args[i + 1]
            i += 2
        elif args[i] == "--status" and i + 1 < len(args):
            status = args[i + 1]
            i += 2
        else:
            i += 1
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key, cfg.cms_email, cfg.cms_password)
    fields: dict[str, Any] = {
        "voiceId": voice_id,
        "pageType": "functionPage",
        "pipelineStatus": status,
    }
    if name:
        fields["name"] = name
    if slug:
        fields["canonicalSlug"] = slug
    try:
        created = cms.create_record(fields)
    except CmsError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "action": "create",
        "id": created.get("id"),
        "voiceId": created.get("voiceId"),
        "pipelineStatus": created.get("pipelineStatus"),
    }


def voices_update(cfg: Config, args: list[str]) -> dict[str, Any]:
    if len(args) < 3 or args[1] != "--set":
        return {
            "ok": False,
            "error": "usage: voices update <id> --set '<json>' [--status STATUS]",
        }
    record_id = args[0]
    if not record_id.isdigit():
        return {"ok": False, "error": "voices update expects a numeric CMS record id"}
    try:
        fields = json.loads(args[2])
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid --set JSON: {exc}"}
    if not isinstance(fields, dict):
        return {"ok": False, "error": "--set JSON must be an object"}
    status = None
    i = 3
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            status = args[i + 1]
            i += 2
        else:
            i += 1
    if status:
        fields["pipelineStatus"] = status
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key, cfg.cms_email, cfg.cms_password)
    try:
        updated = cms.patch_record(int(record_id), fields)
    except CmsError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "action": "update",
        "id": updated.get("id"),
        "voiceId": updated.get("voiceId"),
        "pipelineStatus": updated.get("pipelineStatus"),
    }


def voices(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "usage: voices list|get <key>|create <voiceId>|update <id>"}
    sub = args[0]
    if sub == "get":
        return voices_get(cfg, args[1:])
    if sub == "list":
        return voices_list(cfg, args[1:])
    if sub == "create":
        return voices_create(cfg, args[1:])
    if sub == "update":
        return voices_update(cfg, args[1:])
    return {"ok": False, "error": f"unknown voices subcommand {sub!r} (list|get|create|update)"}


def check(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "usage: check <id|voiceId|slug>"}
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key)
    try:
        record = cms.get_record(args[0], depth=2)
    except CmsError as exc:
        return {"ok": False, "error": str(exc)}
    result = run_checks(record, cfg.site_base)
    return {"ok": result["ok"], **result}


def queue(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not cfg.voices_db_url:
        return {
            "ok": False,
            "error": "NOIZ_VOICES_DB_URL not set (read-only DB connection required)",
        }
    try:
        counts = queue_counts(cfg.voices_db_url)
    except DbError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **counts}


def dry_run(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not args:
        return {
            "ok": False,
            "error": "usage: dry-run <enqueue <voiceId>|consume>",
        }
    if not cfg.voices_db_url:
        return {"ok": False, "error": "NOIZ_VOICES_DB_URL not set"}
    sub = args[0]
    if sub == "enqueue":
        if len(args) < 2:
            return {"ok": False, "error": "usage: dry-run enqueue <voiceId>"}
        voice_id = args[1]
        try:
            exists = queue_voice_exists(cfg.voices_db_url, voice_id)
        except DbError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "dry_run": True,
            "action": "enqueue",
            "voice_id": voice_id,
            "would": "skipped_dup" if exists else "enqueued",
        }
    if sub == "consume":
        try:
            counts = queue_counts(cfg.voices_db_url)
        except DbError as exc:
            return {"ok": False, "error": str(exc)}
        pending = counts["counts"].get("pending", 0)
        return {
            "ok": True,
            "dry_run": True,
            "action": "consume",
            "note": "actual consume runs inside the internal poll_runner/orchestrator",
            "pending": pending,
            "in_progress": counts["counts"].get("in_progress", 0),
        }
    return {"ok": False, "error": f"unknown dry-run target {sub!r} (enqueue|consume)"}


def enqueue(cfg: Config, args: list[str]) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "usage: enqueue <voiceId>"}
    if not cfg.voices_db_url:
        return {"ok": False, "error": "NOIZ_VOICES_DB_URL not set"}
    voice_id = args[0]
    try:
        result = enqueue_voice(cfg.voices_db_url, voice_id)
    except DbError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "action": "enqueue", "voice_id": voice_id, "result": result}


def audit(cfg: Config, args: list[str]) -> dict[str, Any]:
    since = None
    caller = None
    limit = 100
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--since" and i + 1 < len(args):
            since = args[i + 1]
            i += 2
        elif a == "--caller" and i + 1 < len(args):
            caller = args[i + 1]
            i += 2
        elif a == "--limit" and i + 1 < len(args):
            try:
                limit = max(1, int(args[i + 1]))
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    rows = query(cfg.audit_log, since=since, caller=caller, limit=limit)
    return {"ok": True, "log": str(cfg.audit_log), "entries": rows}


COMMANDS = {
    "doctor": doctor,
    "status": status,
    "voices": voices,
    "check": check,
    "queue": queue,
    "dry-run": dry_run,
    "enqueue": enqueue,
    "audit": audit,
    "permissions": permissions,
}
