import json

from noiz_pseo_voice.cms import CmsClient


def test_get_record_by_voice_id_uses_flat_where(monkeypatch):
    """v0.3.0 regression: get/check sent {"where":{"where":{...}}} → CMS 400.
    list_records() already wraps filters under params["where"], so get_record
    must pass a flat filter ({field: {"equals": key}})."""
    client = CmsClient("https://example.invalid/seo-manage")
    captured = {}

    def fake_request(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"docs": [{"voiceId": "abc123", "pipelineStatus": "built"}], "totalDocs": 1}

    monkeypatch.setattr(client, "_request", fake_request)
    doc = client.get_record("abc123")

    assert doc["voiceId"] == "abc123"
    assert captured["path"] == "/voice-detail-pages"
    where = json.loads(captured["params"]["where"])
    # Flat: {"voiceId": {"equals": "abc123"}} — never {"where": {...}} again.
    assert where == {"voiceId": {"equals": "abc123"}}
    assert "where" not in where


def test_get_record_by_numeric_id_uses_direct_endpoint(monkeypatch):
    client = CmsClient("https://example.invalid/seo-manage")
    captured = {}

    def fake_request(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"doc": {"id": 356, "voiceId": "v356"}}

    monkeypatch.setattr(client, "_request", fake_request)
    doc = client.get_record("356")

    assert doc["id"] == 356
    assert captured["path"] == "/voice-detail-pages/356"
    assert captured["params"] == {"depth": 2, "draft": "false"}
