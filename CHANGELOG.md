# Changelog

## v0.6.7 — 2026-08-10

- `voices search` falls back to CMS voice-detail-pages (voiceId/name
  contains) when NOIZ_VOICES_DB_URL is not configured — agents only need CMS
  credentials

## v0.6.6 — 2026-08-10

- Fixed `voices search` db test fake cursor context-manager support (test-only)

## v0.6.5 — 2026-08-10

- Added `voices search <query>`: discover voice_ids by display_name/voice_id
  fuzzy match in the voices DB (feeds `voice-to-page --voice-id`)

## v0.6.3 — 2026-08-10

- URL publicize hooks support an optional Bearer token via
  `NOIZ_PUBLICIZE_TOKEN` (prod visibility API contract)

## v0.6.2 — 2026-08-09

- Voice create hook non-zero exit now surfaces stderr/stdout tail in the
  needs_review reason (quota/validation failures are actionable instead of
  "no output")

## v0.6.1 — 2026-08-09

- Fixed C-tier flag parsing: hyphenated flags (`--ref-audio`,
  `--poll-interval`) now map to underscore payload keys correctly

## v0.6.0 — 2026-08-09

- Added C-tier `voice-to-page --ref-audio <file|url>`: audio → clone hook
  (`NOIZ_VOICE_CREATE_HOOK`, same JSON `--input` contract, returns
  `voice_id`) → A-tier pipeline → check; character/source pairing required
  for real-person compliance

## v0.5.7 — 2026-08-08

- Publicize/alert command hooks: the voice_id is appended as the final
  positional argument; scripts needing a flag should end the configured hook
  with it (e.g. `... --apply --voice-id`)

## v0.5.6 — 2026-08-08

- B-tier voice create hook: parse full multi-line JSON output (fallback to
  last-line JSONL); default hook timeout raised to 600s (voice design/clone
  takes 2-3 min)

## v0.5.5 — 2026-08-08

- B-tier voice create hook: command hooks now receive the payload as a temp
  JSON file via `--input <path>` (voice_design_clone.py contract) instead of
  two positional args; env is configurable via `NOIZ_VOICE_CREATE_ENV`
  (default test)

## v0.5.4 — 2026-08-08

- B-tier voice create hook: `status=needs_review` (e.g. low preview score)
  maps to needs_review exit 2 with the hook's reason, not a hard error

## v0.5.3 — 2026-08-08

- Added B-tier `voice-to-page`: keyword + character/source → voice create hook
  (`NOIZ_VOICE_CREATE_HOOK`, voice_design_clone.py) → A-tier pipeline → check
- PRD v0.5.2 input rules: character/source pairing, 20-500 char description
  (draft + `--confirm-description`), zh/ja/es language requirement, scene
  prefill table (11 scenes), keyword→voice shortcut, `--input <json>`

## v0.5.2 — 2026-08-08

- Fixed `voice-to-page` crash with int record ids: `get_record` now coerces
  the key to `str` before `.strip()` (AttributeError: 'int' object has no
  attribute 'strip')

## v0.5.1 — 2026-08-08

- Fixed `get_record` slug fallback: slug inputs now query `canonicalSlug`
  (including the `voice/` prefix variant) instead of the non-queryable `slug`
  path, which returned CMS 400 (`voices get <slug>` / `check <slug>` /
  `voice-to-page`)

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
