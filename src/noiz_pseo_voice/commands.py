"""Command implementations. Every command returns a dict for --json output."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
import urllib.request
from typing import Any, Optional

from . import __version__
from .audit import append, query
from .checks import run_checks
from .cms import CmsClient, CmsError
from .config import Config
from .db import DbError, enqueue_voice, queue_counts, queue_voice_exists, voice_by_id


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


def _run_hook(hook: str, payload: dict[str, Any], timeout: int = 30) -> str:
    """Call a configured hook: http(s) URL → POST JSON; else executable path."""
    if hook.startswith(("http://", "https://")):
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return f"hook HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            return f"hook HTTP {exc.code}"
        except Exception as exc:
            return f"hook failed: {exc}"
    argv = shlex.split(hook) + [payload.get("voice_id", ""), payload.get("reason", "")]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return f"hook exit {proc.returncode}"


def _needs_review(
    reason: str,
    voice_id: str,
    steps: list[dict[str, Any]],
    alert_hook: Optional[str] = None,
) -> dict[str, Any]:
    if alert_hook:
        _run_hook(
            alert_hook,
            {"voice_id": voice_id, "reason": reason, "status": "needs_review"},
        )
    return {
        "ok": False,
        "exit_code": 2,
        "voice_id": voice_id,
        "status": "needs_review",
        "reason": reason,
        "steps": steps,
        "error": reason,
    }


def voice_to_page(cfg: Config, args: list[str]) -> dict[str, Any]:
    """M1 / A-tier: specified existing voiceId → auto-generated landing page.

    Reuses the existing pipeline: ensure_voice (voices DB) → publicize hook →
    create candidate (CMS) → poll until built/live → check. Idempotent,
    --dry-run, per-step audit, cost accounting fields.
    """
    voice_id = ""
    name = None
    index = True
    dry_run = False
    poll_interval = 20
    timeout = 1800
    reserved = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--voice-id" and i + 1 < len(args):
            voice_id = args[i + 1]
            i += 2
        elif a == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif a == "--index" and i + 1 < len(args):
            index = args[i + 1].lower() not in ("false", "0", "no")
            i += 2
        elif a == "--dry-run":
            dry_run = True
            i += 1
        elif a == "--poll-interval" and i + 1 < len(args):
            poll_interval = max(1, int(args[i + 1]))
            i += 2
        elif a == "--timeout" and i + 1 < len(args):
            timeout = max(10, int(args[i + 1]))
            i += 2
        elif a in ("--description", "--character", "--source", "--ref-audio"):
            reserved.append(a)
            i += 2 if i + 1 < len(args) and not args[i + 1].startswith("--") else 1
        else:
            return {
                "ok": False,
                "error": f"unknown voice-to-page argument {a!r} "
                "(usage: --voice-id <id> [--name] [--index] [--dry-run])",
            }
    if not voice_id:
        return {"ok": False, "error": "voice-to-page requires --voice-id <id> (A-tier)"}
    if reserved:
        return {
            "ok": False,
            "error": "B/C tier (--description/--character/--source/--ref-audio) "
            "not implemented yet in v0.5.0; use --voice-id",
        }

    steps: list[dict[str, Any]] = []

    def add_step(name: str, status: str, detail: str = "") -> None:
        steps.append({"step": name, "status": status, "detail": detail})

    # 1. ensure_voice
    if not cfg.voices_db_url:
        return {
            "ok": False,
            "error": "NOIZ_VOICES_DB_URL required for voice-to-page "
            "(ensure_voice validation)",
        }
    try:
        voice = voice_by_id(cfg.voices_db_url, voice_id)
    except DbError as exc:
        return {"ok": False, "error": str(exc)}
    if voice is None:
        add_step("ensure_voice", "error", "voice_id not found in voices DB")
        return _needs_review(
            f"voice {voice_id} not found in voices DB", voice_id, steps, cfg.alert_hook
        )
    if voice.get("delete_time") is not None:
        add_step("ensure_voice", "error", "voice is deleted")
        return {"ok": False, "exit_code": 1, "voice_id": voice_id, "steps": steps,
                "error": f"voice {voice_id} is deleted"}
    add_step(
        "ensure_voice",
        "ok",
        f"{voice.get('display_name') or voice_id} ({voice.get('language') or '?'})",
    )

    # 2. publicize
    is_public = bool(voice.get("is_public")) and voice.get("status") == "active"
    if is_public:
        add_step("publicize", "skipped", "already public/active")
    elif dry_run:
        add_step("publicize", "dry_run", "would call NOIZ_PUBLICIZE_HOOK")
    elif cfg.publicize_hook:
        detail = _run_hook(cfg.publicize_hook, {"voice_id": voice_id})
        add_step("publicize", "requested", detail)
    else:
        add_step(
            "publicize",
            "needs_review",
            "voice not public and NOIZ_PUBLICIZE_HOOK not configured "
            "(test: 后台 conversion script; prod: visibility API)",
        )
        return _needs_review(
            f"voice {voice_id} is not public; 后台 must convert to built-in/public "
            "(NOIZ_PUBLICIZE_HOOK not configured)",
            voice_id,
            steps,
            cfg.alert_hook,
        )

    # 3. candidate
    cms = CmsClient(cfg.cms_url, cfg.cms_api_key, cfg.cms_email, cfg.cms_password)
    record = None
    try:
        record = cms.get_record(voice_id, depth=1)
    except CmsError as exc:
        if "no voice-detail record found" not in str(exc):
            add_step("candidate", "error", str(exc))
            return {"ok": False, "exit_code": 1, "voice_id": voice_id, "steps": steps,
                    "error": str(exc)}
    record_id = None
    if record is None:
        if dry_run:
            add_step("candidate", "dry_run", "would create candidate record")
        else:
            fields: dict[str, Any] = {
                "voiceId": voice_id,
                "pageType": "functionPage",
                "pipelineStatus": "candidate_screening",
                "index": index,
            }
            if name:
                fields["name"] = name
            try:
                created = cms.create_record(fields)
                record_id = created.get("id") or (created.get("doc") or {}).get("id")
                add_step("candidate", "ok", f"created record {record_id}")
            except CmsError as exc:
                add_step("candidate", "error", str(exc))
                return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                        "steps": steps, "error": str(exc)}
    else:
        record_id = record.get("id")
        add_step("candidate", "skipped", f"existing record {record_id} ({record.get('pipelineStatus')})")

    # 4. pipeline poll
    status = record.get("pipelineStatus") if record else "candidate_screening"
    if dry_run:
        add_step("pipeline", "dry_run", f"would drive record {record_id} to built")
        return {
            "ok": True,
            "exit_code": 0,
            "dry_run": True,
            "voice_id": voice_id,
            "record_id": record_id,
            "pipeline_status": status,
            "steps": steps,
            "cost": {"voice_design": 0, "clone": 0, "demo": None},
        }
    if status not in ("built", "live"):
        add_step("pipeline", "in_progress", f"initial status {status}; polling")
        deadline = time.time() + timeout
        last = status
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                rec = cms.get_record(record_id, depth=1)
            except CmsError as exc:
                add_step("pipeline", "error", str(exc))
                return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                        "record_id": record_id, "steps": steps, "error": str(exc)}
            status = rec.get("pipelineStatus") or last
            gate = rec.get("contentGate")
            if status != last:
                add_step("pipeline", "in_progress", f"{last} → {status}")
                last = status
            if status in ("built", "live"):
                break
            if status == "rejected":
                add_step("pipeline", "error", "record rejected")
                return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                        "record_id": record_id, "steps": steps,
                        "error": f"record rejected (contentGate={gate})"}
            if gate == "needs_review":
                return _needs_review(
                    f"voice {voice_id} flagged needs_review by content gate",
                    voice_id,
                    steps,
                    cfg.alert_hook,
                )
            if gate == "blocked":
                add_step("pipeline", "error", "content gate blocked")
                return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                        "record_id": record_id, "steps": steps,
                        "error": "record blocked by content gate"}
        if status not in ("built", "live"):
            add_step("pipeline", "error", f"timeout after {timeout}s (last status {status})")
            return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                    "record_id": record_id, "steps": steps,
                    "error": f"pipeline did not reach built within {timeout}s (last {status})"}
        add_step("pipeline", "ok", "built/live")

    # 5. final check
    try:
        full = cms.get_record(record_id, depth=2)
    except CmsError as exc:
        return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                "record_id": record_id, "steps": steps, "error": str(exc)}
    check_result = run_checks(full, cfg.site_base)
    checks = check_result["checks"]
    failing = [c["name"] for c in checks if not c["ok"]]
    add_step("check", "ok" if not failing else "failed", ",".join(failing) or "all PASS")
    assets = ((full.get("pipelineStaging") or {}).get("assets") or [])

    if failing:
        if "assets_nonempty" in failing:
            return _needs_review(
                f"voice {voice_id} has no demo assets (无素材音色)",
                voice_id,
                steps,
                cfg.alert_hook,
            )
        return {"ok": False, "exit_code": 1, "voice_id": voice_id,
                "record_id": record_id, "pipeline_status": status,
                "steps": steps, "checks": checks,
                "page_url": check_result.get("page_url"),
                "error": f"check failed: {','.join(failing)}"}

    return {
        "ok": True,
        "exit_code": 0,
        "voice_id": voice_id,
        "record_id": record_id,
        "pipeline_status": status,
        "steps": steps,
        "checks": checks,
        "page_url": check_result.get("page_url"),
        "cost": {
            "voice_design": 0,
            "clone": 0,
            "demo": len(assets) if assets else None,
            "note": "A-tier reuses existing pipeline; API costs recorded by internal orchestrator",
        },
    }


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
    "voice-to-page": voice_to_page,
}
