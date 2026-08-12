from noiz_pseo_voice.config import Config
from noiz_pseo_voice import config as config_mod


def test_env_override_defaults(monkeypatch):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_CMS_API_KEY", "k")
    cfg = Config()
    assert cfg.cms_url == "https://cms.example/seo-manage"
    assert cfg.cms_api_key == "k"


def test_legacy_scope_env_ignored(monkeypatch):
    """v0.4.0: NOIZ_CLI_SCOPE read/write tiers are gone; the variable is
    ignored instead of rejected so old configs keep loading."""
    monkeypatch.setenv("NOIZ_CLI_SCOPE", "read")
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    cfg = Config()
    assert not hasattr(cfg, "cli_scope_override")


def test_cms_url_required(monkeypatch):
    monkeypatch.delenv("NOIZ_CMS_URL", raising=False)
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    try:
        Config()
    except ValueError as exc:
        assert "NOIZ_CMS_URL is required" in str(exc)
        return
    raise AssertionError("missing NOIZ_CMS_URL should raise")


def test_default_config_file_autoload(monkeypatch, tmp_path):
    cfg_file = tmp_path / "noiz-pseo-voice.env"
    cfg_file.write_text("NOIZ_SITE_BASE=https://from-file.example\n", encoding="utf-8")
    cfg_file.chmod(0o600)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_FILE", cfg_file)
    monkeypatch.delenv("NOIZ_PSEO_VOICE_CONFIG", raising=False)
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_VOICES_DB_URL", "postgresql://x")
    cfg = Config()
    assert cfg.site_base == "https://from-file.example"
    # env still wins over the auto-loaded file.
    monkeypatch.setenv("NOIZ_SITE_BASE", "https://env.example")
    assert Config().site_base == "https://env.example"


def test_explicit_empty_disables_autoload(monkeypatch, tmp_path):
    cfg_file = tmp_path / "noiz-pseo-voice.env"
    cfg_file.write_text("NOIZ_SITE_BASE=https://from-file.example\n", encoding="utf-8")
    cfg_file.chmod(0o600)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_FILE", cfg_file)
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_VOICES_DB_URL", "postgresql://x")
    cfg = Config()
    assert cfg.site_base == "https://noiz.ai"
