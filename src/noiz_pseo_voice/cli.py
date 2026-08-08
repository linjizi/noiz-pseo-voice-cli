"""Console entry point for noiz-pseo-voice."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from . import __version__
from .commands import COMMANDS
from .config import Config


def _render_text(result: dict[str, Any], command: str) -> str:
    ok = result.get("ok", False)
    lines: list[str] = []
    if command == "doctor":
        for c in result.get("checks", []):
            lines.append(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
        lines.append(f"overall: {'OK' if ok else 'FAIL'}")
    elif command == "status":
        lines.append(f"cms: {result.get('cms_url', '')}")
        lines.append(f"records by pipelineStatus: {json.dumps(result.get('by_pipeline_status', {}), ensure_ascii=False)}")
        queue = result.get("queue", {})
        if "counts" in queue:
            lines.append(f"queue: {json.dumps(queue['counts'], ensure_ascii=False)}")
            lines.append(f"cursor: {queue.get('cursor')} (updated {queue.get('cursor_updated_at')})")
        elif "note" in queue:
            lines.append(f"queue: {queue['note']}")
        if "queue_error" in result:
            lines.append(f"queue error: {result['queue_error']}")
    elif command == "voices":
        if "error" in result:
            lines.append(f"error: {result['error']}")
        elif "action" in result:
            lines.append(
                f"{result['action']} ok: id={result.get('id')} "
                f"voiceId={result.get('voiceId')} status={result.get('pipelineStatus')}"
            )
        elif "record" in result:
            rec = result["record"]
            lines.append(
                f"id={rec.get('id')} voiceId={rec.get('voiceId')} "
                f"status={rec.get('pipelineStatus')} "
                f"slug={rec.get('canonicalSlug') or rec.get('slug') or '-'}"
            )
        else:
            lines.append(f"total {result['total']}, returned {result['returned']}")
            for v in result.get("voices", []):
                lines.append(
                    f"{v['id']}\t{v['voiceId']}\t{v['status']}\t{v['slug'] or '-'}\t{v['updatedAt'] or '-'}"
                )
    elif command == "check":
        if "error" in result:
            lines.append(f"error: {result['error']}")
        else:
            for c in result.get("checks", []):
                lines.append(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
            lines.append(f"overall: {'OK' if ok else 'FAIL'} (voiceId={result.get('voice_id')})")
            if result.get("page_url"):
                lines.append(f"page: {result['page_url']}")
    elif command == "queue":
        if "error" in result:
            lines.append(f"error: {result['error']}")
        else:
            lines.append(f"counts: {json.dumps(result['counts'], ensure_ascii=False)}")
            lines.append(f"cursor: {result.get('cursor')} (updated {result.get('cursor_updated_at')})")
    elif command == "dry-run":
        if "error" in result:
            lines.append(f"error: {result['error']}")
        else:
            lines.append(f"dry-run {result.get('action')}: {json.dumps({k: v for k, v in result.items() if k not in ('ok', 'dry_run', 'action')}, ensure_ascii=False)}")
    elif command == "enqueue":
        if "error" in result:
            lines.append(f"error: {result['error']}")
        else:
            lines.append(f"enqueue {result['voice_id']}: {result['result']}")
    elif command == "audit":
        if "error" in result:
            lines.append(f"error: {result['error']}")
        else:
            lines.append(f"log: {result['log']} ({len(result['entries'])} entries)")
            for e in result["entries"]:
                lines.append(
                    f"{e.get('t')}\t{e.get('caller') or '-'}\t{e.get('command')}\t"
                    f"{'OK' if e.get('ok') else 'FAIL'}\t{json.dumps(e.get('args', []), ensure_ascii=False)}"
                )
    elif command == "permissions":
        lines.append(f"auth: {result.get('auth')}")
        lines.append(f"model: {result.get('model')}")
    else:
        lines.append(json.dumps(result, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="noiz-pseo-voice",
        description="CLI for the Noiz voice SEO pipeline (external agent friendly).",
    )
    parser.add_argument("--json", action="store_true", help="structured JSON output")
    parser.add_argument("--version", action="version", version=f"noiz-pseo-voice {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="environment/credential self-check")
    sub.add_parser("status", help="pipeline overview (CMS + optional queue)")
    sub.add_parser("permissions", help="verify CMS account auth (any valid account: full CLI, voice-detail-pages only)")
    sub.add_parser("queue", help="queue counts + cursor (requires DB)")
    p_audit = sub.add_parser("audit", help="local audit log query")
    p_audit.add_argument("--since", help="only entries at/after this ISO timestamp")
    p_audit.add_argument("--caller", help="only entries from this caller id")
    p_audit.add_argument("--limit", type=int, default=100, help="max entries (default 100)")

    p_voices = sub.add_parser("voices", help="voice-detail record operations")
    vsub = p_voices.add_subparsers(dest="voices_sub", required=True)
    p_list = vsub.add_parser("list", help="list records")
    p_list.add_argument("--status", help="filter by pipelineStatus")
    p_list.add_argument("--locale", help="filter by locale")
    p_list.add_argument("--limit", type=int, default=50, help="max rows (default 50)")
    p_get = vsub.add_parser("get", help="get one record by id/voiceId/slug")
    p_get.add_argument("key", help="id, voiceId or slug")
    p_create = vsub.add_parser("create", help="create a candidate record (any authenticated account)")
    p_create.add_argument("voice_id", help="voiceId from the voices DB")
    p_create.add_argument("--name", help="optional display name")
    p_create.add_argument("--slug", help="optional canonicalSlug")
    p_create.add_argument("--status", default="candidate_screening", help="initial pipelineStatus")
    p_update = vsub.add_parser("update", help="patch fields on a record (any authenticated account)")
    p_update.add_argument("record_id", help="numeric CMS record id")
    p_update.add_argument("--set", required=True, metavar="JSON", help="fields to patch as JSON object")
    p_update.add_argument("--status", help="shortcut to set pipelineStatus")

    p_check = sub.add_parser("check", help="acceptance checks for one record")
    p_check.add_argument("key", help="id, voiceId or slug")

    p_dry = sub.add_parser("dry-run", help="preview a write command without changing state")
    dsub = p_dry.add_subparsers(dest="dry_target", required=True)
    p_dry_enq = dsub.add_parser("enqueue", help="preview queue enqueue")
    p_dry_enq.add_argument("voice_id")
    dsub.add_parser("consume", help="preview queue consumption")

    p_enq = sub.add_parser("enqueue", help="enqueue a voice into the pipeline queue (any authenticated account)")
    p_enq.add_argument("voice_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_flag = "--json" in argv
    clean_argv = [a for a in argv if a != "--json"]
    parser = build_parser()
    try:
        args = parser.parse_args(clean_argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        cfg = Config()
        command = args.command
        if command == "voices":
            fn = COMMANDS["voices"]
            if args.voices_sub == "list":
                rest = ["list"]
                if args.status:
                    rest += ["--status", args.status]
                if args.locale:
                    rest += ["--locale", args.locale]
                rest += ["--limit", str(args.limit)]
            elif args.voices_sub == "get":
                rest = ["get", args.key]
            elif args.voices_sub == "create":
                rest = ["create", args.voice_id]
                if args.name:
                    rest += ["--name", args.name]
                if args.slug:
                    rest += ["--slug", args.slug]
                rest += ["--status", args.status]
            else:
                rest = ["update", args.record_id, "--set", args.set]
                if args.status:
                    rest += ["--status", args.status]
        elif command == "dry-run":
            fn = COMMANDS["dry-run"]
            rest = [args.dry_target]
            if args.dry_target == "enqueue":
                rest.append(args.voice_id)
        else:
            fn = COMMANDS[command]
            if command == "check":
                rest = [args.key]
            elif command == "enqueue":
                rest = [args.voice_id]
            elif command == "audit":
                rest = []
                if args.since:
                    rest += ["--since", args.since]
                if args.caller:
                    rest += ["--caller", args.caller]
                rest += ["--limit", str(args.limit)]
            else:
                rest = []
        result = fn(cfg, rest)
    except Exception as exc:  # keep agent-facing output machine-readable
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        command = getattr(args, "command", "unknown")
        rest = []

    try:
        from .audit import append
        append(
            cfg.audit_log,
            {
                "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "caller": cfg.caller_id,
                "command": command,
                "args": rest,
                "ok": bool(result.get("ok", False)),
            },
        )
    except Exception:
        pass  # audit must never break the command result

    if json_flag:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        text = _render_text(result, command)
        print(text)
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
