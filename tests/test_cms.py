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


def test_get_record_accepts_int_record_id(monkeypatch):
    """v0.5.2 regression: voice-to-page polls with int record_id; get_record
    must coerce to str instead of crashing on .strip()."""
    client = CmsClient("https://example.invalid/seo-manage")
    captured = {}

    def fake_request(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"doc": {"id": 385, "voiceId": "v385", "pipelineStatus": "built"}}

    monkeypatch.setattr(client, "_request", fake_request)
    doc = client.get_record(385)

    assert doc["id"] == 385
    assert captured["path"] == "/voice-detail-pages/385"


def test_get_record_by_slug_maps_to_canonical_slug(monkeypatch):
    """v0.5.1 regression: slug input must query canonicalSlug (with the
    voice/ prefix variant) — Payload 400s on where[slug]."""
    client = CmsClient("https://example.invalid/seo-manage")
    captured = []

    def fake_request(path, params=None):
        captured.append((path, params))
        where = json.loads(params["where"]) if params and params.get("where") else {}
        field = next(iter(where), None)
        if (
            field == "canonicalSlug"
            and where["canonicalSlug"]["equals"] == "voice/my-slug"
        ):
            return {"docs": [{"voiceId": "v1", "canonicalSlug": "voice/my-slug"}], "totalDocs": 1}
        return {"docs": [], "totalDocs": 0}

    monkeypatch.setattr(client, "_request", fake_request)
    doc = client.get_record("my-slug")

    assert doc["voiceId"] == "v1"
    fields = []
    for _, params in captured:
        where = json.loads(params["where"]) if params.get("where") else {}
        fields.append(next(iter(where), None))
    assert fields == ["voiceId", "canonicalSlug", "canonicalSlug"]
    assert "slug" not in fields
    assert fields.count("canonicalSlug") == 2
