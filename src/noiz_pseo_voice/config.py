"""Configuration loading for the CLI.

Precedence: environment variables > optional 0600 key=value config file
(path via NOIZ_PSEO_VOICE_CONFIG). All secrets stay out of the repository.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

DEFAULT_SITE_BASE = "https://noiz.ai"
# Per-user convenience config (0600 key=value) loaded when
# NOIZ_PSEO_VOICE_CONFIG is unset; environment variables still win.
DEFAULT_CONFIG_FILE = Path.home() / ".config" / "noiz-pseo-voice.env"


def _read_config_file(path: Optional[str]) -> dict[str, str]:
    if not path:
        return {}
    cfg_path = Path(path).expanduser()
    if not cfg_path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in cfg_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _config_file_mode_warning(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    cfg_path = Path(path).expanduser()
    if not cfg_path.is_file():
        return None
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    if mode & 0o077:
        return (
            f"config file {cfg_path} is group/world readable (mode {oct(mode)}); "
            "chmod 600 to protect credentials"
        )
    return None


class Config:
    def __init__(self) -> None:
        env = dict(os.environ)
        explicit = env.get("NOIZ_PSEO_VOICE_CONFIG")
        if explicit is not None:
            # Empty string = explicitly disable config-file loading.
            file_path = explicit or None
            file_vars = _read_config_file(file_path)
        else:
            default = DEFAULT_CONFIG_FILE
            file_path = str(default) if default.is_file() else None
            file_vars = _read_config_file(file_path)
        merged = {**file_vars, **env}  # env wins

        self.config_file = file_path
        self.config_warning = _config_file_mode_warning(file_path)
        raw_cms_url = merged.get("NOIZ_CMS_URL", "").strip()
        if not raw_cms_url:
            raise ValueError(
                "NOIZ_CMS_URL is required (set it to your Payload CMS base, "
                "e.g. https://cms.example.com/seo-manage)"
            )
        self.cms_url = raw_cms_url.rstrip("/")
        self.cms_api_key = merged.get("NOIZ_CMS_API_KEY", "").strip() or None
        self.cms_email = merged.get("NOIZ_CMS_EMAIL", "").strip() or None
        self.cms_password = merged.get("NOIZ_CMS_PASSWORD", "").strip() or None
        self.site_base = (
            merged.get("NOIZ_SITE_BASE", DEFAULT_SITE_BASE).rstrip("/")
        )
        self.voices_db_url = merged.get("NOIZ_VOICES_DB_URL", "").strip() or None
        # Public voice-library explore API (no credentials needed) used by
        # `voices search` when NOIZ_VOICES_DB_URL is not configured.
        self.explore_base = (
            merged.get("NOIZ_EXPLORE_BASE", "https://noiz.ai").strip().rstrip("/")
            or "https://noiz.ai"
        )
        self.caller_id = merged.get("NOIZ_CALLER_ID", "").strip() or None
        # voice-to-page publicization hook: an http(s) URL (POST JSON
        # {"voice_id": ...}) or an executable path (called with voice_id as
        # argv[1]). Test phase = 后台's idempotent conversion script; prod
        # phase = visibility API once it exists.
        self.publicize_hook = merged.get("NOIZ_PUBLICIZE_HOOK", "").strip() or None
        # Optional alert hook for needs_review outcomes (e.g. Slock channel
        # notification). Same http URL / executable path convention.
        self.alert_hook = merged.get("NOIZ_ALERT_HOOK", "").strip() or None
        # B-tier voice create hook (voice_design_clone.py): URL POST JSON or
        # shell command; must return JSON with voice_id.
        self.voice_create_hook = merged.get("NOIZ_VOICE_CREATE_HOOK", "").strip() or None
        # Optional Bearer token for URL publicize hooks (prod visibility API).
        self.publicize_token = merged.get("NOIZ_PUBLICIZE_TOKEN", "").strip() or None
        # Target environment for the B-tier voice create hook (default test).
        self.voice_create_env = merged.get("NOIZ_VOICE_CREATE_ENV", "test").strip() or "test"
        # C-tier post-clone Gemini labeling helper (SEO-side, per 《音色库标签》).
        self.tag_labels_script = (
            merged.get("NOIZ_TAG_LABELS_SCRIPT", "").strip() or ""
        )
        default_audit = Path.home() / ".local" / "share" / "noiz-pseo-voice" / "audit.jsonl"
        self.audit_log = Path(
            merged.get("NOIZ_AUDIT_LOG", str(default_audit))
        ).expanduser()
