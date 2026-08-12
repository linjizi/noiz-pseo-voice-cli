import noiz_pseo_voice.commands as commands
import noiz_pseo_voice.cli as cli
from noiz_pseo_voice import db
from noiz_pseo_voice.config import Config


def _cfg(monkeypatch):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    monkeypatch.setenv("NOIZ_VOICES_DB_URL", "postgresql://x")
    monkeypatch.delenv("NOIZ_CMS_API_KEY", raising=False)
    monkeypatch.delenv("NOIZ_CMS_EMAIL", raising=False)
    monkeypatch.delenv("NOIZ_CMS_PASSWORD", raising=False)
    return Config()


def test_search_requires_query(monkeypatch):
    result = commands.voices_search(_cfg(monkeypatch), [])
    assert result["ok"] is False
    assert "usage" in result["error"]


def test_search_falls_back_to_explore_without_db_url(monkeypatch):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    monkeypatch.delenv("NOIZ_VOICES_DB_URL", raising=False)
    cfg = Config()

    monkeypatch.setattr(
        commands, "_explore_search",
        lambda base, query, limit: [{"voice_id": "v1", "display_name": "Narrator"}],
    )
    result = commands.voices_search(cfg, ["nar", "--limit", "5"])
    assert result["ok"] is True
    assert result["source"] == "explore"
    assert result["voices"][0]["voice_id"] == "v1"


def test_search_explore_empty_falls_back_to_cms(monkeypatch):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    monkeypatch.delenv("NOIZ_VOICES_DB_URL", raising=False)
    cfg = Config()
    monkeypatch.setattr(commands, "_explore_search", lambda base, query, limit: [])

    class FakeCms:
        def list_records(self, filters, limit=100, depth=0, page=1):
            return (
                [{"voiceId": "v1", "name": "Narrator", "canonicalSlug": "voice/narrator", "pipelineStatus": "built"}],
                1,
            )

    fake = FakeCms()
    monkeypatch.setattr(commands, "CmsClient", lambda *a, **k: fake)
    result = commands.voices_search(cfg, ["nar"])
    assert result["ok"] is True
    assert result["source"] == "cms"


def test_explore_search_parses_keyword_response(monkeypatch):
    import urllib.request

    captured = {}

    class FakeResp:
        def read(self):
            return b'{"data":{"voices":[{"voice_id":"v1","display_name":"Narrator","is_public":true,"voice_type":"built-in","meta":{"language":"en"}}]}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rows = commands._explore_search("https://noiz.ai", "nar reader", 7)
    assert rows[0]["voice_id"] == "v1"
    assert rows[0]["language"] == "en"
    assert "keyword=nar%20reader" in captured["url"]
    assert "limit=7" in captured["url"]


def test_search_text_render_does_not_require_total():
    # Hermy 2026-08-10: text mode crashed with KeyError 'total' because the
    # list renderer was reused; search returns {returned, voices} not {total}.
    result = {
        "ok": True,
        "query": "nar",
        "source": "explore",
        "returned": 1,
        "voices": [{"voice_id": "v1", "display_name": "Narrator", "language": "en"}],
    }
    text = cli._render_text(result, "voices")
    assert "search nar" in text
    assert "v1" in text
    assert "Narrator" in text


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

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

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
