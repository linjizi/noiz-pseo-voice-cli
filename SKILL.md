---
name: "noiz-pseo-voice"
description: "Query and operate the Noiz voice SEO pipeline from the command line: pipeline status, voice-detail records, voice_id discovery, per-page acceptance checks, queue inspection, manual record create/update, idempotent enqueue, and voice-to-page orchestration (A: existing voiceId, B: keyword+description creates a voice, C: reference audio clones one). Use when an agent needs to inspect or modify voice SEO pages (voice-detail-pages) through the Payload CMS REST API, verify a generated page, or create a landing page from a voice, keyword, or audio file. Authentication is a CMS account (email/password or API key); command scope is limited to the voice-detail-pages chain."
---

# noiz-pseo-voice CLI

Command-line client for the Noiz voice SEO pipeline. It talks to the Payload
CMS REST API (`voice-detail-pages` collection), the voices database (optional),
and the public voice-library API. Every command supports `--json`; exit code is
0 on success, 1 on error, 2 on `needs_review`.

## Install

```bash
pip install "git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.6.13"
# DB-backed commands (queue / dry-run / enqueue) need the extra dependency:
pip install "noiz-pseo-voice-cli[db] @ git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.6.13"
```

## Configure

Credentials live in the environment or in a 0600 config file pointed to by
`NOIZ_PSEO_VOICE_CONFIG` (env vars win). `NOIZ_CMS_URL` is required.

```bash
NOIZ_CMS_URL=https://cms.example.com/seo-manage
NOIZ_CMS_EMAIL=user@example.com       # or NOIZ_CMS_API_KEY=...
NOIZ_CMS_PASSWORD=secret
NOIZ_SITE_BASE=https://example.com    # for page-liveness checks
NOIZ_VOICES_DB_URL=postgresql://readonly:xxx@host:5432/db   # optional; queue/search full library
NOIZ_PUBLICIZE_HOOK=https://noiz.ai/api/v1/voices/<voice_id>/publicize  # URL hook; or shell command
NOIZ_PUBLICIZE_TOKEN=...              # optional Bearer token for URL publicize hook
NOIZ_VOICE_CREATE_HOOK="python /path/voice_design_clone.py --env test"  # B/C voice creation
NOIZ_VOICE_CREATE_ENV=test            # default test; set prod only when authorized
NOIZ_CALLER_ID=my-agent
```

Missing/expired credentials fail closed with a clear error; write commands
record an audit entry and can be previewed with `dry-run`.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor` | Environment/credential self-check (CMS reachability, auth, DB, audit) |
| `permissions` | Verify account auth (any valid account can run all commands) |
| `status` | Pipeline overview: CMS status counts + queue counts/cursor |
| `voices list [--status] [--locale] [--limit]` | List CMS records |
| `voices search <query> [--limit]` | Discover voice_ids by name/voice_id (DB → public explore API → CMS) |
| `voices get <id\|voiceId\|slug>` | Fetch one record (depth=2) |
| `voices create <voiceId> [--name] [--slug] [--status]` | Create a candidate record |
| `voices update <id> --set '<json>' [--status]` | Patch fields / advance pipeline status |
| `voice-to-page --voice-id <id>` | A-tier: existing voice → landing page |
| `voice-to-page --keyword ... --character ... --source ...` | B-tier: create a voice from a description → page |
| `voice-to-page --ref-audio <file\|url>` | C-tier: clone from reference audio → page |
| `check <id\|voiceId\|slug>` | Per-page acceptance checks (status/keywords/hash/assets/URL) |
| `queue` | Queue counts + cursor (needs DB) |
| `dry-run enqueue <voiceId>` / `dry-run consume` | Preview without changing state |
| `enqueue <voiceId>` | Idempotently enqueue a voice into the pipeline queue |
| `audit [--since] [--caller] [--limit]` | Query the local audit log |

## Workflows

**Health check and voice discovery:**

```bash
noiz-pseo-voice doctor --json
noiz-pseo-voice voices search "narrator" --limit 10   # get voice_id from a name
```

**A-tier: existing voice → landing page:**

```bash
noiz-pseo-voice voice-to-page --voice-id <voiceId> --dry-run
noiz-pseo-voice voice-to-page --voice-id <voiceId>
noiz-pseo-voice check <voiceId> --json
```

**B-tier: keyword/description creates a voice → page (character/source paired):**

```bash
noiz-pseo-voice voice-to-page --keyword "calm wellness narrator" \
  --character "Calm Wellness Narrator" --source "Original character" \
  --description "young female, calm and gentle, wellness narration, English" \
  --scene wellness --dry-run
```

**C-tier: reference audio → clone → page:**

```bash
noiz-pseo-voice voice-to-page --ref-audio /path/sample.mp3 --name "My Voice" --dry-run
```

**Verify a page / manual writes / audit:**

```bash
noiz-pseo-voice check <voiceId> --json
noiz-pseo-voice dry-run enqueue <voiceId> && noiz-pseo-voice enqueue <voiceId>
noiz-pseo-voice audit --since 2026-08-08T00:00:00Z
```

## Rules and safety

- Never print, log, or paste credentials; they come only from env or the 0600
  config file.
- Permission model: the CMS has no role tiers, so any valid account can run all
  CLI commands; scope is limited to the voice-detail-pages chain.
- Prefer `--json` for machine-readable output; use `--dry-run` before any write.
- `voice-to-page` exit codes: 0 = created/ok, 1 = error, 2 = needs_review
  (non-public voice, content-gate review, or no demo assets — includes a
  `reason` and optionally fires `NOIZ_ALERT_HOOK`).
- B-tier: `--character` and `--source` must be provided together; `--language`
  is required for zh/ja/es keywords; `--scene` prefills gender/age/labels.
- C-tier: `--character` requires `--source` (real-person compliance).
- Write commands are always recorded in the local audit log — check `audit`
  before and after.
- If `check` reports a FAIL, inspect the record with `voices get` before
  retrying; keyword data may be a JSON string inside `keywordInputJson`.
