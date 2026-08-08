from noiz_pseo_voice.config import Config


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
    try:
        Config()
    except ValueError as exc:
        assert "NOIZ_CMS_URL is required" in str(exc)
        return
    raise AssertionError("missing NOIZ_CMS_URL should raise")
