# Changelog

## v0.5.0 — 2026-08-08

- Added `voice-to-page` (A-tier): specified existing voiceId → auto landing page (ensure_voice → publicize hook → candidate/pipeline poll → check)
- Idempotent re-runs, per-step audit, `--dry-run`, `--json`, exit code 2 = needs_review
- Configurable `NOIZ_PUBLICIZE_HOOK` / `NOIZ_ALERT_HOOK`; B/C tier flags reserved
- Cost accounting fields (`voice_design` / `clone` / `demo`) in output

## v0.4.1 — 2026-08-08

- Removed hardcoded internal CMS URL defaults; `NOIZ_CMS_URL` is now required
- Sanitized test credentials/emails; repo clean of internal references

## v0.4.0 — 2026-08-08

- Permission model aligned with CMS: authenticated accounts can run all commands (voice-detail-pages scope only); removed read/write tiers (`NOIZ_CLI_SCOPE`)
- Added credential verification (`doctor` auth item, `permissions`)
- Kept audit log and `dry-run`

## v0.3.2 — 2026-08-08

- `check` now parses `keywordInputJson` stored as a JSON string before reading `primary_keyword`/`validated`

## v0.3.1 — 2026-08-08

- Fixed `voices get` / `check` 400 error: `where` filter was double-nested; now flat

## v0.3.0 — 2026-08-08

- First prod-ready release after full acceptance (read + write commands)

## v0.2.0 — 2026-08-08

- Added manual pipeline writes: `voices create`, `voices update`; JWT login support

## v0.1.0 — 2026-08-08

- Initial release: read-only commands, audit log, `--json` output
