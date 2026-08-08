import json

from noiz_pseo_voice.checks import run_checks


def _record(status="built", kw=None, content_hash="abc", assets=("a",), slug="x"):
    return {
        "voiceId": "v1",
        "pipelineStatus": status,
        "canonicalSlug": slug,
        "pipelineStaging": {
            "keywordInputJson": kw or {"primary_keyword": "kw", "validated": True},
            "generatedContentHash": content_hash,
            "assets": list(assets),
        },
    }


def test_built_record_passes(monkeypatch):
    monkeypatch.setattr("noiz_pseo_voice.checks._url_ok", lambda url, timeout=15: True)
    result = run_checks(_record(), "https://noiz.ai")
    assert result["ok"] is True
    names = {c["name"] for c in result["checks"]}
    assert "status_terminal" in names
    assert "page_url_live" in names


def test_pending_record_fails():
    result = run_checks(_record(status="pending_keywords"), "https://noiz.ai")
    assert result["ok"] is False
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["status_terminal"]["ok"] is False


def test_missing_fields_fail():
    result = run_checks({"voiceId": "v2", "pipelineStatus": "built"}, "https://noiz.ai")
    assert result["ok"] is False
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["keywords_present"]["ok"] is False
    assert by_name["assets_nonempty"]["ok"] is False


def test_keyword_input_json_string_is_parsed():
    """v0.3.2: keywordInputJson is a serialized JSON string in CMS (text
    field); check must parse it instead of treating it as an empty dict."""
    kw = {"primary_keyword": "elegant host voice ai", "validated": True, "source": "db"}
    result = run_checks(_record(kw=json.dumps(kw)), "https://noiz.ai")
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["keywords_present"]["ok"] is True
    assert by_name["primary_keyword"]["ok"] is True
    assert by_name["primary_keyword"]["detail"] == "elegant host voice ai"
    assert by_name["keywords_validated"]["ok"] is True


def test_invalid_keyword_json_fails():
    result = run_checks(_record(kw="{not json"), "https://noiz.ai")
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["primary_keyword"]["ok"] is False
    assert by_name["keywords_validated"]["ok"] is False
    assert "not valid JSON" in by_name["keywords_present"]["detail"]
