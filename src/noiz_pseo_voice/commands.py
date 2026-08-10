"""Command implementations. Every command returns a dict for --json output."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.request
from typing import Any, Optional

from . import __version__
from .audit import append, query
from .checks import run_checks
from .cms import CmsClient, CmsError
from .config import Config
from .db import (
    DbError,
    enqueue_voice,
    queue_counts,
    queue_voice_exists,
    search_voices,
    voice_by_id,
)


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


def voices_search(cfg: Config, args: list[str]) -> dict[str, Any]:
    """Search the voices DB by name/voice_id to discover ids for
    voice-to-page (cathan 2026-08-10)."""
    if not args:
        return {"ok": False, "error": "usage: voices search <query> [--limit N]"}
    query = args[0]
    limit = 20
    i = 1
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = max(1, min(100, int(args[i + 1])))
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    if not cfg.voices_db_url:
        return {"ok": False, "error": "NOIZ_VOICES_DB_URL required for voices search"}
    try:
        rows = search_voices(cfg.voices_db_url, query, limit)
    except DbError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "query": query, "returned": len(rows), "voices": rows}


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
        return {"ok": False, "error": "usage: voices list|search <query>|get <key>|create <voiceId>|update <id>"}
    sub = args[0]
    if sub == "get":
        return voices_get(cfg, args[1:])
    if sub == "list":
        return voices_list(cfg, args[1:])
    if sub == "search":
        return voices_search(cfg, args[1:])
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


def _run_hook(
    hook: str,
    payload: dict[str, Any],
    timeout: int = 30,
    token: Optional[str] = None,
) -> str:
    """Call a configured hook: http(s) URL → POST JSON; else executable path."""
    if hook.startswith(("http://", "https://")):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode(),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return f"hook HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            return f"hook HTTP {exc.code}"
        except Exception as exc:
            return f"hook failed: {exc}"
    # Command hooks receive the voice_id as the final positional argument.
    # Scripts that need a flag (e.g. convert_to_public.py) should include it in
    # the configured hook string: "... --apply --voice-id" (v0.5.7).
    argv = shlex.split(hook) + [payload.get("voice_id", "")]
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


def _run_hook_json(hook: str, payload: dict[str, Any], timeout: int = 600) -> Optional[dict[str, Any]]:
    """Call a hook and parse its JSON output (voice-create hook contract:
    returns {"voice_id": ...}). URL → POST JSON body; command → payload written
    to a temp JSON file and passed as --input <path> (voice_design_clone.py
    contract, v0.5.5)."""
    try:
        if hook.startswith(("http://", "https://")):
            req = urllib.request.Request(
                hook,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        else:
            fd, tmp_path = tempfile.mkstemp(prefix="noiz-v2p-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            argv = shlex.split(hook) + ["--input", tmp_path]
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
                body = proc.stdout
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if proc.returncode != 0:
                # v0.6.2: surface the hook's own diagnostics instead of a bare
                # "no output" so quota/validation failures are actionable.
                tail = (proc.stderr or proc.stdout or "").strip()[-300:]
                return {
                    "status": "needs_review",
                    "reason": f"voice create hook exit {proc.returncode}: {tail or 'no output'}",
                }
        # voice_design_clone.py prints indent=2 multi-line JSON; parse the
        # whole body first, fall back to the last non-empty line (JSONL-style
        # hooks) (v0.5.6).
        stripped = body.strip()
        try:
            out = json.loads(stripped) if stripped else {}
        except json.JSONDecodeError:
            out = {}
            for line in reversed(stripped.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    out = json.loads(line)
                except json.JSONDecodeError:
                    continue
                break
        return out if isinstance(out, dict) else None
    except Exception:
        return None


# PRD v0.5.2 appendix: scene → gender/age/labels prefill (数据运营, 2026-08-08).
SCENE_PREFILLS: dict[str, dict[str, Any]] = {
    "social_video": {"gender": "neutral", "age": "young", "labels": ["social", "energetic", "modern"]},
    "gaming": {"gender": "neutral", "age": "young", "labels": ["gaming", "dramatic"]},
    "education": {"gender": "neutral", "age": "adult", "labels": ["education", "professional"]},
    "podcast": {"gender": "neutral", "age": "adult", "labels": ["podcast", "conversational"]},
    "advertising": {"gender": "neutral", "age": "young", "labels": ["advertising", "persuasive"]},
    "audiobook": {"gender": "neutral", "age": "adult", "labels": ["audiobook", "storytelling"]},
    "wellness": {"gender": "female", "age": "young", "labels": ["wellness", "calm", "empathetic"]},
    "sports": {"gender": "neutral", "age": "adult", "labels": ["sports", "energetic", "commentator"]},
    "entertainment": {"gender": "neutral", "age": "young", "labels": ["entertainment", "playful"]},
    "drama": {"gender": "neutral", "age": "adult", "labels": ["drama", "emotional"]},
    "anime": {"gender": "female", "age": "young", "labels": ["anime", "kawaii"]},
}

# Description five-dimension hints for the soft check (PRD v0.5.2).
DESCRIPTION_DIM_HINTS = {
    "gender/age": ("male", "female", "young", "old", "middle", "child", "boy", "girl", "man", "woman"),
    "accent/dialect": ("accent", "british", "american", "mandarin", "japanese", "dialect", "english"),
    "tone/mood": ("calm", "warm", "energetic", "dramatic", "authoritative", "gentle", "serious", "playful", "excited", "emotional"),
    "style/scene": ("narration", "story", "anime", "podcast", "commercial", "audiobook", "tutorial", "game", "social", "wellness"),
    "language": ("chinese", "mandarin", "japanese", "english", "spanish", "zh", "ja", "es", "en"),
}


def _load_b_input(raw: str) -> tuple[dict[str, Any], Optional[str]]:
    """--input <json>: file path or inline JSON object. Returns (data, error)."""
    text = raw.strip()
    if not text.startswith("{"):
        try:
            with open(text, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            return {}, f"cannot read --input file {raw!r}: {exc}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"--input is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return {}, "--input must be a JSON object"
    return data, None


def _validate_b_input(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a merged B-tier input."""
    errors: list[str] = []
    warnings: list[str] = []
    if not data.get("keyword"):
        errors.append("B mode requires --keyword (or input.keyword)")
    character = data.get("character") or ""
    source = data.get("source") or ""
    if bool(character) != bool(source):
        errors.append("--character and --source must be provided together")
    desc = (data.get("description") or "").strip()
    if desc and not (20 <= len(desc) <= 500):
        errors.append(f"--description length must be 20-500 chars (got {len(desc)})")
    if not desc:
        warnings.append("--description missing; run --dry-run to preview the generated draft")
    elif desc:
        missing = []
        for dim, hints in DESCRIPTION_DIM_HINTS.items():
            lowered = desc.lower()
            if not any(h in lowered for h in hints):
                missing.append(dim)
        if missing:
            warnings.append("description may miss dimensions: " + ", ".join(missing))
    target_language = data.get("target_language") or data.get("language")
    if target_language in ("zh", "ja", "es") and not data.get("language"):
        errors.append("--language is required when keyword locale is zh/ja/es")
    return errors, warnings


def _voice_to_page_b(cfg: Config, args: list[str]) -> dict[str, Any]:
    """B-tier: keyword (+character/source) → voice design/clone hook → A flow."""
    raw: dict[str, Any] = {}
    input_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--input" and i + 1 < len(args):
            input_path = args[i + 1]
            i += 2
        elif a in ("--keyword", "--character", "--source", "--description", "--gender",
                   "--age", "--labels", "--language", "--scene", "--volume", "--tier",
                   "--related", "--name", "--confirm-description", "--dry-run",
                   "--poll-interval", "--timeout", "--index"):
            key = a.lstrip("-")
            if a == "--confirm-description":
                raw["confirm_description"] = True
            elif a == "--dry-run":
                raw["dry_run"] = True
            elif i + 1 < len(args):
                value = args[i + 1]
                raw[key] = value
                i += 2
                continue
            i += 1
        elif a == "--voice-id":
            return {"ok": False, "error": "--voice-id is A-tier; use B-tier inputs"}
        else:
            return {"ok": False, "error": f"unknown B-tier argument {a!r}"}

    data: dict[str, Any] = {}
    if input_path:
        loaded, err = _load_b_input(input_path)
        if err:
            return {"ok": False, "error": err}
        data.update(loaded)
    # Flags override JSON values.
    for key, value in raw.items():
        if key in ("confirm_description", "dry_run"):
            data[key] = value
        else:
            data[key] = value

    dry_run = bool(data.get("dry_run"))
    errors, warnings = _validate_b_input(data)
    if errors:
        return {"ok": False, "exit_code": 1, "steps": [], "error": "; ".join(errors)}

    steps: list[dict[str, Any]] = []

    def add_step(name: str, status: str, detail: str = "") -> None:
        steps.append({"step": name, "status": status, "detail": detail})

    keyword = str(data["keyword"]).strip()
    character = str(data.get("character") or "").strip()
    source = str(data.get("source") or "").strip()
    description = str(data.get("description") or "").strip()
    scene = str(data.get("scene") or "").strip().lower()
    name = str(data.get("name") or "").strip() or None
    poll_interval = int(data.get("poll-interval") or data.get("poll_interval") or 20)
    timeout = int(data.get("timeout") or 1800)
    index = str(data.get("index") or "true").lower() not in ("false", "0", "no")

    # Scene prefill (flags/JSON win over prefill).
    if scene and scene in SCENE_PREFILLS:
        pre = SCENE_PREFILLS[scene]
        for key in ("gender", "age"):
            if not data.get(key):
                data[key] = pre[key]
        if not data.get("labels"):
            data["labels"] = ",".join(pre["labels"])
        add_step("scene_prefill", "ok", f"prefilled from scene={scene}")

    # keyword→voice shortcut (keyword-explorer export with an existing voice).
    if data.get("voice_id"):
        voice_id = str(data["voice_id"])
        add_step("keyword_shortcut", "ok", f"keyword already has voice {voice_id}; downgrading to A-tier")
    elif description:
        voice_id = None
        add_step("description", "ok", "user-provided description")
    else:
        draft = (
            f"A {data.get('gender') or 'versatile'} {data.get('age') or ''} voice for "
            f"{character or keyword} from {source or 'open source'}, suited for "
            f"{scene or 'content'} narration, clear and expressive."
        ).strip()
        add_step("description", "dry_run" if dry_run else "needs_review",
                 f"generated draft (confirm with --confirm-description or pass --description): {draft}")
        if dry_run:
            add_step("voice_create", "dry_run",
                     "would call NOIZ_VOICE_CREATE_HOOK after description confirmed")
            return {
                "ok": True,
                "exit_code": 0,
                "dry_run": True,
                "b_input": {k: data[k] for k in data if k != "confirm_description"},
                "description_draft": draft,
                "warnings": warnings,
                "steps": steps,
            }
        if not data.get("confirm_description"):
            return _needs_review(
                "B mode requires --description or --confirm-description for the generated draft",
                keyword,
                steps,
                cfg.alert_hook,
            )
        description = draft
        data["description"] = description

    if voice_id is None:
        if dry_run:
            add_step("voice_create", "dry_run", "would call NOIZ_VOICE_CREATE_HOOK")
            return {
                "ok": True,
                "exit_code": 0,
                "dry_run": True,
                "b_input": {k: data[k] for k in data if k != "confirm_description"},
                "steps": steps,
                "warnings": warnings,
            }
        if not cfg.voice_create_hook:
            add_step("voice_create", "needs_review",
                     "NOIZ_VOICE_CREATE_HOOK not configured (B-tier needs voice_design_clone.py)")
            return _needs_review(
                "B-tier voice create hook not configured; set NOIZ_VOICE_CREATE_HOOK "
                "(voice_design_clone.py) or use --voice-id (A-tier)",
                keyword,
                steps,
                cfg.alert_hook,
            )
        payload = {
            "keyword": keyword,
            "character": character,
            "source": source,
            "description": description,
            "gender": data.get("gender"),
            "age": data.get("age"),
            "labels": data.get("labels"),
            "language": data.get("language"),
            "scene": scene,
            "env": cfg.voice_create_env,
        }
        result = _run_hook_json(cfg.voice_create_hook, payload)
        if result and result.get("status") == "needs_review":
            # PRD v0.5.3: low preview score etc. → needs_review (exit 2), not a
            # hard error; propagate the hook's reason when available.
            reason = (
                str(result.get("reason") or result.get("detail") or "")
                or "voice create hook returned needs_review"
            )
            add_step("voice_create", "needs_review", reason)
            return _needs_review(reason, keyword, steps, cfg.alert_hook)
        if not result or not result.get("voice_id"):
            add_step("voice_create", "needs_review", "hook did not return voice_id")
            return _needs_review(
                f"voice create hook failed to return voice_id ({result or 'no output'})",
                keyword,
                steps,
                cfg.alert_hook,
            )
        voice_id = str(result["voice_id"])
        add_step("voice_create", "ok", f"created voice {voice_id} via hook")

    # Delegate to A-tier flow with the (existing/new) voice_id.
    a_args = ["--voice-id", voice_id]
    if name:
        a_args += ["--name", name]
    a_args += ["--index", "true"]
    a_args[-1] = str(index)
    if dry_run:
        a_args += ["--dry-run"]
    a_args += ["--poll-interval", str(poll_interval), "--timeout", str(timeout)]
    a_result = _voice_to_page_a(cfg, a_args)

    steps += a_result.get("steps", [])
    a_result["steps"] = steps
    a_result["page_hint"] = {
        "name_base": name or keyword or character,
        "slug_base": keyword or character,
    }
    a_result["b_input"] = {k: data[k] for k in data if k != "confirm_description"}
    if warnings:
        a_result["warnings"] = warnings
    return a_result


def _voice_to_page_c(cfg: Config, args: list[str]) -> dict[str, Any]:
    """C-tier: reference audio → clone hook → A flow (PRD v0.6, M3)."""
    raw: dict[str, Any] = {}
    input_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--input" and i + 1 < len(args):
            input_path = args[i + 1]
            i += 2
        elif a in ("--ref-audio", "--name", "--language", "--labels", "--gender",
                   "--age", "--character", "--source", "--poll-interval",
                   "--timeout", "--index"):
            if i + 1 < len(args):
                raw[a.lstrip("-").replace("-", "_")] = args[i + 1]
                i += 2
                continue
            i += 1
        elif a == "--dry-run":
            raw["dry_run"] = True
            i += 1
        elif a == "--voice-id":
            return {"ok": False, "error": "--voice-id is A-tier; use --ref-audio (C-tier)"}
        else:
            return {"ok": False, "error": f"unknown C-tier argument {a!r}"}

    data: dict[str, Any] = {}
    if input_path:
        loaded, err = _load_b_input(input_path)
        if err:
            return {"ok": False, "error": err}
        data.update(loaded)
    for key, value in raw.items():
        data[key] = value

    dry_run = bool(data.get("dry_run"))
    ref_audio = str(data.get("ref_audio") or "").strip()
    character = str(data.get("character") or "").strip()
    source = str(data.get("source") or "").strip()
    if not ref_audio:
        return {"ok": False, "exit_code": 1, "error": "C-tier requires --ref-audio <file|url>"}
    if bool(character) != bool(source):
        return {
            "ok": False, "exit_code": 1,
            "error": "--character and --source must be provided together (real-person compliance)",
        }

    steps: list[dict[str, Any]] = []

    def add_step(name: str, status: str, detail: str = "") -> None:
        steps.append({"step": name, "status": status, "detail": detail})

    add_step("ref_audio", "ok", ref_audio)

    if not cfg.voice_create_hook:
        if dry_run:
            add_step("clone", "dry_run", "would call NOIZ_VOICE_CREATE_HOOK (audio clone contract)")
            return {
                "ok": True, "exit_code": 0, "dry_run": True,
                "c_input": data, "steps": steps,
            }
        add_step("clone", "needs_review", "NOIZ_VOICE_CREATE_HOOK not configured")
        return _needs_review(
            "C-tier audio clone hook not configured; set NOIZ_VOICE_CREATE_HOOK "
            "(audio clone script) or use --voice-id (A-tier)",
            ref_audio,
            steps,
            cfg.alert_hook,
        )
    if dry_run:
        add_step("clone", "dry_run", "would call NOIZ_VOICE_CREATE_HOOK (audio clone contract)")
        return {
            "ok": True, "exit_code": 0, "dry_run": True,
            "c_input": data, "steps": steps,
        }

    payload = {
        "ref_audio": ref_audio,
        "name": data.get("name"),
        "language": data.get("language"),
        "labels": data.get("labels"),
        "gender": data.get("gender"),
        "age": data.get("age"),
        "character": character or None,
        "source": source or None,
        "env": cfg.voice_create_env,
    }
    result = _run_hook_json(cfg.voice_create_hook, payload)
    if result and result.get("status") == "needs_review":
        reason = (
            str(result.get("reason") or result.get("detail") or "")
            or "audio clone hook returned needs_review"
        )
        add_step("clone", "needs_review", reason)
        return _needs_review(reason, ref_audio, steps, cfg.alert_hook)
    if not result or not result.get("voice_id"):
        add_step("clone", "needs_review", "hook did not return voice_id")
        return _needs_review(
            f"audio clone hook failed to return voice_id ({result or 'no output'})",
            ref_audio,
            steps,
            cfg.alert_hook,
        )
    voice_id = str(result["voice_id"])
    add_step("clone", "ok", f"cloned voice {voice_id} via hook")

    a_args = ["--voice-id", voice_id]
    if data.get("name"):
        a_args += ["--name", str(data["name"])]
    a_args += ["--index", str(data.get("index") or "true")]
    if dry_run:
        a_args += ["--dry-run"]
    a_args += [
        "--poll-interval", str(data.get("poll-interval") or 20),
        "--timeout", str(data.get("timeout") or 1800),
    ]
    a_result = _voice_to_page_a(cfg, a_args)
    steps += a_result.get("steps", [])
    a_result["steps"] = steps
    a_result["page_hint"] = {
        "name_base": data.get("name") or voice_id,
        "slug_base": data.get("name") or voice_id,
    }
    a_result["c_input"] = data
    return a_result


def voice_to_page(cfg: Config, args: list[str]) -> dict[str, Any]:
    """A-tier (--voice-id) or B-tier (keyword/character/source) orchestration."""
    if "--ref-audio" in args:
        return _voice_to_page_c(cfg, args)
    b_markers = {"--input", "--keyword", "--character", "--source", "--scene",
                 "--volume", "--tier", "--related", "--gender", "--age",
                 "--language", "--confirm-description"}
    if any(a in b_markers for a in args):
        return _voice_to_page_b(cfg, args)
    return _voice_to_page_a(cfg, args)


def _voice_to_page_a(cfg: Config, args: list[str]) -> dict[str, Any]:
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
        elif a in ("--description", "--character", "--source"):
            reserved.append(a)
            i += 2 if i + 1 < len(args) and not args[i + 1].startswith("--") else 1
        elif a == "--ref-audio":
            return {"ok": False, "error": "C-tier (--ref-audio) is a separate mode"}
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
            "error": "B tier inputs belong to B mode; provide --keyword/--character/"
            "--source together (or use --voice-id for A-tier)",
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
        detail = _run_hook(
            cfg.publicize_hook,
            {"voice_id": voice_id},
            token=cfg.publicize_token,
        )
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
