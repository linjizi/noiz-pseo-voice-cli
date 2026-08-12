import json

import noiz_pseo_voice.commands as commands
from noiz_pseo_voice.commands import voices_create, voices_update
from noiz_pseo_voice.config import Config


def _cfg(monkeypatch) -> Config:
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    monkeypatch.delenv("NOIZ_CMS_API_KEY", raising=False)
    monkeypatch.delenv("NOIZ_CMS_EMAIL", raising=False)
    monkeypatch.delenv("NOIZ_CMS_PASSWORD", raising=False)
    return Config()


class FakeCms:
    """v0.4.0: login success is the gate — the client itself is the auth."""

    def __init__(self, *args, **kwargs):
        pass

    def create_record(self, fields):
        return {
            "id": 99,
            "voiceId": fields["voiceId"],
            "pipelineStatus": fields["pipelineStatus"],
        }

    def patch_record(self, record_id, fields):
        return {"id": record_id, "voiceId": "v1", **fields}


def test_create_proceeds_with_valid_credentials(monkeypatch):
    cfg = _cfg(monkeypatch)
    monkeypatch.setattr(commands, "CmsClient", FakeCms)
    result = voices_create(cfg, ["abc123"])
    assert result["ok"] is True
    assert result["id"] == 99
    assert result["voiceId"] == "abc123"


def test_update_rejects_bad_json(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = voices_update(cfg, ["356", "--set", "{not json"])
    assert result["ok"] is False
    assert "invalid --set JSON" in result["error"]


def test_update_rejects_non_object(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = voices_update(cfg, ["356", "--set", json.dumps([1, 2])])
    assert result["ok"] is False
    assert "must be an object" in result["error"]


def test_update_proceeds_with_valid_credentials(monkeypatch):
    cfg = _cfg(monkeypatch)
    monkeypatch.setattr(commands, "CmsClient", FakeCms)
    result = voices_update(cfg, ["356", "--set", json.dumps({"name": "x"})])
    assert result["ok"] is True
    assert result["id"] == 356


def test_enqueue_requires_db_not_scope(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.enqueue(cfg, ["abc123"])
    assert result["ok"] is False
    assert "NOIZ_VOICES_DB_URL" in result["error"]
