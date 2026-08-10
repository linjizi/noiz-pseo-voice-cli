import noiz_pseo_voice.commands as commands
from noiz_pseo_voice import db
from noiz_pseo_voice.config import Config


def _cfg(monkeypatch):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_VOICES_DB_URL", "postgresql://x")
    monkeypatch.delenv("NOIZ_CMS_API_KEY", raising=False)
    monkeypatch.delenv("NOIZ_CMS_EMAIL", raising=False)
    monkeypatch.delenv("NOIZ_CMS_PASSWORD", raising=False)
    return Config()


def test_search_requires_query(monkeypatch):
    result = commands.voices_search(_cfg(monkeypatch), [])
    assert result["ok"] is False
    assert "usage" in result["error"]


def test_search_requires_db_url(monkeypatch):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.delenv("NOIZ_VOICES_DB_URL", raising=False)
    cfg = Config()
    result = commands.voices_search(cfg, ["nar"])
    assert result["ok"] is False
    assert "NOIZ_VOICES_DB_URL" in result["error"]


def test_search_returns_rows(monkeypatch):
    cfg = _cfg(monkeypatch)
    rows = [
        {"voice_id": "v1", "display_name": "Narrator", "language": "en",
         "is_public": True, "status": "active", "voice_type": "built-in"},
    ]
    monkeypatch.setattr(commands, "search_voices", lambda dsn, query, limit=20: rows)
    result = commands.voices_search(cfg, ["nar", "--limit", "5"])
    assert result["ok"] is True
    assert result["voices"] == rows
    assert result["returned"] == 1


def test_search_voices_sql_shape(monkeypatch):
    class FakeCur:
        def __init__(self):
            self.calls = []
            self.rows = [("v1", "Narrator", "en", True, "active", "built-in")]

        def execute(self, sql, params=None):
            self.calls.append((sql, params))

        def fetchall(self):
            return self.rows

    class FakeConn:
        def __init__(self):
            self.cur = FakeCur()

        def cursor(self):
            return self.cur

        def close(self):
            pass

    fake = FakeConn()
    monkeypatch.setattr(db, "_connect", lambda dsn: fake)
    rows = db.search_voices("postgresql://x", "nar", limit=10)
    assert rows[0]["voice_id"] == "v1"
    sql, params = fake.cur.calls[0]
    assert "(display_name ILIKE %s OR voice_id ILIKE %s)" in sql
    assert "delete_time IS NULL" in sql
    assert "LIMIT %s" in sql
    assert params == ("%nar%", "%nar%", 10)
