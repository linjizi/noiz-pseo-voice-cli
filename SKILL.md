---
name: "noiz-pseo-voice"
description: "Query and operate the Noiz voice SEO pipeline from the command line: pipeline status, voice-detail records, per-page acceptance checks, queue inspection, manual record create/update, and idempotent enqueue. Use when an agent needs to inspect or modify voice SEO pages (voice-detail-pages) through the Payload CMS REST API, verify a generated page, or enqueue a voice for the pipeline. Requires CMS credentials (email/password or API key); all commands are limited to the voice-detail-pages chain."
---

# noiz-pseo-voice CLI

Command-line client for the Noiz voice SEO pipeline. It talks to the Payload
CMS REST API (`voice-detail-pages` collection) and, for queue commands, to the
voices database. Every command supports `--json`; exit code is 0 on success,
1 on failure.

## Install

```bash
pip install "git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.4.1"
# DB-backed commands (queue / dry-run / enqueue) need the extra dependency:
pip install "noiz-pseo-voice-cli[db] @ git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.4.1"
```

## Configure

Credentials live in the environment or in a 0600 config file pointed to by
`NOIZ_PSEO_VOICE_CONFIG` (env vars win).

```bash
NOIZ_CMS_URL=https://cms.example.com/seo-manage
NOIZ_CMS_EMAIL=user@example.com       # or NOIZ_CMS_API_KEY=...
NOIZ_CMS_PASSWORD=secret
NOIZ_SITE_BASE=https://example.com    # for page-liveness checks
NOIZ_VOICES_DB_URL=postgresql://readonly:xxx@host:5432/db   # queue commands only
NOIZ_CALLER_ID=my-agent
```

`NOIZ_CMS_URL` is required. Missing/expired credentials fail closed with a
clear error; write commands still record an audit entry and can be previewed
with `dry-run`.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor` | Environment/credential self-check (CMS reachability, auth, DB, audit) |
| `permissions` | Verify account auth (any valid account can run all commands) |
| `status` | Pipeline overview: CMS status counts + queue counts/cursor |
| `voices list [--status] [--locale] [--limit]` | List records |
| `voices get <id\|voiceId\|slug>` | Fetch one record (depth=2) |
| `voices create <voiceId> [--name] [--slug] [--status]` | Create a candidate record |
| `voices update <id> --set '<json>' [--status]` | Patch fields / advance pipeline status |
| `check <id\|voiceId\|slug>` | Per-page acceptance checks (status/keywords/hash/assets/URL) |
| `queue` | Queue counts + cursor (needs DB) |
| `dry-run enqueue <voiceId>` / `dry-run consume` | Preview without changing state |
| `enqueue <voiceId>` | Idempotently enqueue a voice into the pipeline queue |
| `audit [--since] [--caller] [--limit]` | Query the local audit log |

## Workflows

**Quick health check:**

```bash
noiz-pseo-voice doctor --json
noiz-pseo-voice status
```

**Verify a generated voice page:**

```bash
noiz-pseo-voice voices get <voiceId>
noiz-pseo-voice check <voiceId> --json
```

`check` fails unless status is terminal, keywords are present and validated,
content hash and assets exist, and the live page URL returns 2xx/3xx.

**Manual pipeline write (always preview first):**

```bash
noiz-pseo-voice dry-run enqueue <voiceId>
noiz-pseo-voice enqueue <voiceId>                 # repeats are skipped_dup
noiz-pseo-voice voices create <voiceId> --status candidate_screening
noiz-pseo-voice voices update 356 --set '{"pipelineStaging":{"keywordInputJson":{"primary_keyword":"tts","validated":true}}}' --status keywords_ready
```

**Audit trail:**

```bash
noiz-pseo-voice audit --since 2026-08-08T00:00:00Z --caller my-agent
```

## Rules and safety

- Never print, log, or paste credentials; they come only from env or the 0600
  config file.
- Permission model: the CMS has no role tiers, so any valid account can run all
  CLI commands; scope is limited to the voice-detail-pages chain. Do not add
  agent/human distinctions.
- Prefer `--json` for machine-readable output; use `dry-run` before any write.
- Write commands are always recorded in the local audit log — check `audit`
  before and after.
- If `check` reports a FAIL, inspect the record with `voices get` before
  retrying; keyword data may be a JSON string inside `keywordInputJson`.
