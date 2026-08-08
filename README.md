# noiz-pseo-voice-cli

CLI for operating a voice SEO pipeline: query pipeline state, run per-page acceptance checks, and manually create/update voice pages. Works for humans and AI agents alike — authentication is a CMS account, nothing else.

> English: README.md · 简体中文：[README.zh-CN.md](README.zh-CN.md)

## Background

Voice SEO pipelines usually live as internal scripts on a runner machine. This CLI wraps the same operations into a single installable command with structured output, so any caller (CI, automation, agents, operators) can:

- inspect pipeline state and queue health
- run acceptance checks on a voice page (keywords, content hash, assets, live URL)
- manually create a candidate record and write/advance its fields — a manual pipeline without the resident runner
- enqueue a voice into the pipeline (idempotent)
- keep an audit trail of who ran what

## Commands

| Command | Type | Purpose |
| --- | --- | --- |
| `doctor` | read | environment/credential self-check |
| `permissions` | read | verify auth and show the permission model |
| `status` | read | pipeline overview (CMS counts + optional queue) |
| `voices list [--status] [--locale] [--limit]` | read | list records |
| `voices get <id\|voiceId\|slug>` | read | get one record |
| `voices create <voiceId> [--name] [--slug] [--status]` | write | create a candidate record |
| `voices update <id> --set '<json>' [--status]` | write | patch fields / advance status |
| `check <id\|voiceId\|slug>` | read | per-page acceptance check |
| `queue` | read | queue counts + cursor (requires DB) |
| `dry-run enqueue <voiceId>` / `dry-run consume` | read | preview a write without changing state |
| `enqueue <voiceId>` | write | enqueue a voice (idempotent) |
| `audit [--since] [--caller] [--limit]` | read | query the local audit log |

All commands support `--json` for structured output and `--help` for self-discovery. Exit code is `0` on success and `1` on failure.

## Install

```bash
pip install "git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.4.0"
# DB commands (queue / dry-run / enqueue) need the optional dependency:
pip install "noiz-pseo-voice-cli[db] @ git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.4.0"
```

## Quick start

1. Install (above).
2. Create a config file (recommended `~/.config/noiz-pseo-voice.env`, then `chmod 600`):

```bash
NOIZ_CMS_URL=https://your-cms.example.com/seo-manage
NOIZ_CMS_API_KEY=sk-xxx            # or NOIZ_CMS_EMAIL / NOIZ_CMS_PASSWORD
NOIZ_SITE_BASE=https://example.com
NOIZ_VOICES_DB_URL=postgresql://readonly:xxx@host:5432/db
NOIZ_CALLER_ID=my-agent
```

3. Self-check:

```bash
export NOIZ_PSEO_VOICE_CONFIG=~/.config/noiz-pseo-voice.env
noiz-pseo-voice doctor
```

All PASS means you are ready; FAIL entries tell you what is missing.

## Tutorial: 15 minutes

1. **Pipeline overview** — `noiz-pseo-voice status`
2. **Find a record** — `noiz-pseo-voice voices list --status built --limit 10` then `noiz-pseo-voice voices get <id>`
3. **Accept a page** — `noiz-pseo-voice check <voiceId>` (status, keywords, content hash, assets, live URL, itemized PASS/FAIL)
4. **Create a voice page (write)** — `noiz-pseo-voice voices create <voiceId> --name "My Voice" --status candidate_screening`
5. **Write fields / advance status (write)** — `noiz-pseo-voice voices update <id> --set '{"pipelineStaging":{"keywordInputJson":{"primary_keyword":"tts","validated":true}}}' --status keywords_ready`
6. **Enqueue (write)** — `noiz-pseo-voice dry-run enqueue <voiceId>` first, then `noiz-pseo-voice enqueue <voiceId>` (re-running returns `skipped_dup`)
7. **Audit** — `noiz-pseo-voice audit --since 2026-08-08T00:00:00Z`

## Examples

Human-readable:

```bash
$ noiz-pseo-voice check 7bc8b578
[PASS] status_terminal: built
[PASS] keywords_present: keywordInputJson/keywordInput
[PASS] primary_keyword: classic audiobook narrator
[PASS] keywords_validated: validated flag
[PASS] content_hash: a1b2c3...
[PASS] assets_nonempty: 9 asset(s)
[PASS] page_url_defined: https://example.com/lp/voice/...
[PASS] page_url_live: https://example.com/lp/voice/...
overall: OK (voiceId=7bc8b578)
```

Machine-readable:

```bash
$ noiz-pseo-voice check 7bc8b578 --json
{
  "ok": true,
  "checks": [{"name": "status_terminal", "ok": true, "detail": "built"}, ...],
  "voice_id": "7bc8b578",
  "page_url": "https://example.com/lp/voice/..."
}
```

In CI:

```bash
noiz-pseo-voice check "$VOICE_ID" --json || exit 1
```

## Configuration

Precedence: environment variables > key=value file at `NOIZ_PSEO_VOICE_CONFIG` (0600 recommended).

| Variable | Required | Description |
| --- | --- | --- |
| `NOIZ_CMS_URL` | yes | Payload CMS base URL (no internal default) |
| `NOIZ_CMS_API_KEY` | one of | CMS API key |
| `NOIZ_CMS_EMAIL` / `NOIZ_CMS_PASSWORD` | one of | CMS account login |
| `NOIZ_SITE_BASE` | no | base URL for live-page checks (default `https://noiz.ai`) |
| `NOIZ_VOICES_DB_URL` | DB commands | voices database DSN |
| `NOIZ_CALLER_ID` | no | caller identity recorded in the audit log |
| `NOIZ_AUDIT_LOG` | no | audit log path (default `~/.local/share/noiz-pseo-voice/audit.jsonl`) |

## Permission model

- Authentication is a CMS account (API key or email/password). There is no separate agent/human tier.
- The CLI aligns with the CMS: if the account can authenticate, it can run every CLI command. Command scope is limited to the voice-detail-pages pipeline (create/update/enqueue); nothing else is touched.
- Two non-permission safeguards are kept: `dry-run` previews for write commands, and a local audit log for every invocation.
- Store credentials in a 0600 file or environment variables; never commit them.

## Security

- Secrets only in environment variables or 0600 config files
- Audit log per invocation (caller, command, result) — queryable with `audit`
- Write commands are idempotent where possible and previewable with `dry-run`

## Development

```bash
pip install -e ".[dev,db]"
pytest
```

PRD: `docs/PRD.md`. License: MIT (see `LICENSE`).
