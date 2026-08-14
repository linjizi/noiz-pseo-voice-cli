import json
import os

import noiz_pseo_voice.commands as commands
from noiz_pseo_voice.cms import CmsError
from noiz_pseo_voice.config import Config


PUBLIC_VOICE = {
    "id": 1,
    "voice_id": "v1",
    "is_public": True,
    "status": "active",
    "voice_type": "built-in",
    "delete_time": None,
    "display_name": "V One",
    "language": "en",
    "creation_mode": "voice_clone",
}


def _cfg(monkeypatch, **overrides):
    monkeypatch.setenv("NOIZ_CMS_URL", "https://cms.example/seo-manage")
    monkeypatch.setenv("NOIZ_PSEO_VOICE_CONFIG", "")
    monkeypatch.delenv("NOIZ_CMS_API_KEY", raising=False)
    monkeypatch.delenv("NOIZ_CMS_EMAIL", raising=False)
    monkeypatch.delenv("NOIZ_CMS_PASSWORD", raising=False)
    monkeypatch.setenv("NOIZ_VOICES_DB_URL", "postgresql://x")
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return Config()


class FakeCms:
    def __init__(self, *args, **kwargs):
        self.records = {}
        self.patch_calls = []

    def get_record(self, key, depth=1):
        rec = self.records.get(str(key)) or self.records.get(key)
        if rec is None:
            raise CmsError(f"no voice-detail record found for {key!r}")
        return rec

    def create_record(self, fields):
        rid = 500 + len(self.records) // 2
        rec = {
            "id": rid,
            "voiceId": fields["voiceId"],
            "pipelineStatus": fields["pipelineStatus"],
            "pipelineStaging": {},
        }
        self.records[str(rid)] = rec
        self.records[fields["voiceId"]] = rec
        return {"id": rid, **rec}

    def patch_record(self, record_id, fields):
        self.patch_calls.append((record_id, dict(fields)))
        rec = self.records.get(str(record_id)) or self.records.get(record_id)
        if rec is None:
            raise CmsError(f"no voice-detail record found for {record_id!r}")
        rec.update(fields)
        # regen tests end the poll loop immediately by moving to built.
        rec["pipelineStatus"] = "built"
        rec["pipelineStaging"] = {"assets": [{"slug": "a"}] * 9}
        return rec


def _fake_checks(record, site_base):
    staging = record.get("pipelineStaging") or {}
    assets = staging.get("assets") or []
    checks = [
        {
            "name": "status_terminal",
            "ok": record.get("pipelineStatus") in ("built", "live"),
            "detail": str(record.get("pipelineStatus")),
        },
        {"name": "keywords_present", "ok": True, "detail": "ok"},
        {"name": "assets_nonempty", "ok": bool(assets), "detail": f"{len(assets)} asset(s)"},
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "page_url": None,
        "voice_id": record.get("voiceId"),
    }


def _patch_env(monkeypatch, cms=None, voice=None, hook_calls=None):
    if cms is None:
        cms = FakeCms()
    monkeypatch.setattr(commands, "voice_by_id", lambda dsn, vid: voice)
    monkeypatch.setattr(commands, "CmsClient", lambda *args, **kwargs: cms)
    monkeypatch.setattr(commands, "run_checks", _fake_checks)
    monkeypatch.setattr(commands.time, "sleep", lambda s: None)
    if hook_calls is not None:
        def record_hook(hook, payload, timeout=30, token=None):
            hook_calls.append((hook, payload))
            return "hook exit 0"
        monkeypatch.setattr(commands, "_run_hook", record_hook)
    return cms


def _install_built_after_create(monkeypatch, cms):
    def built_after_create(fields):
        cms.last_create_fields = fields
        created = FakeCms.create_record(cms, fields)
        rid = created["id"]
        rec = cms.records[str(rid)]
        rec["pipelineStatus"] = "built"
        rec["pipelineStaging"] = {"assets": [{"slug": "a"}] * 9}
        return {"id": rid, **rec}

    monkeypatch.setattr(cms, "create_record", built_after_create)


def test_requires_voice_id(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(cfg, [])
    assert result["ok"] is False
    assert "--voice-id" in result["error"]


def test_rejects_mixing_a_and_b_args(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1", "--description", "x"])
    assert result["ok"] is False
    assert "A-tier" in result["error"]


def test_requires_db_url(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICES_DB_URL=None)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1"])
    assert result["ok"] is False
    assert "NOIZ_VOICES_DB_URL" in result["error"]


def test_voice_not_found_needs_review(monkeypatch):
    cfg = _cfg(monkeypatch)
    _patch_env(monkeypatch, voice=None)
    result = commands.voice_to_page(cfg, ["--voice-id", "ghost"])
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert result["status"] == "needs_review"


def test_non_public_without_hook_needs_review(monkeypatch):
    voice = dict(PUBLIC_VOICE, is_public=False)
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=voice)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1"])
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "not public" in result["reason"]
    assert len(cms.records) == 0  # no candidate created


def test_non_public_with_hook_proceeds(monkeypatch):
    voice = dict(PUBLIC_VOICE, is_public=False)
    cfg = _cfg(monkeypatch, NOIZ_PUBLICIZE_HOOK="https://hook.example")
    calls = []
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=voice, hook_calls=calls)
    _install_built_after_create(monkeypatch, cms)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1", "--timeout", "60"])
    assert result["ok"] is True
    assert calls and calls[0][1] == {"voice_id": "v1"}
    assert any(s["step"] == "publicize" for s in result["steps"])


def test_dry_run_no_writes(monkeypatch):
    voice = dict(PUBLIC_VOICE, is_public=False)
    cfg = _cfg(monkeypatch, NOIZ_PUBLICIZE_HOOK="https://hook.example")
    calls = []
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=voice, hook_calls=calls)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1", "--dry-run"])
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert calls == []  # hook not called
    assert len(cms.records) == 0  # no candidate created
    statuses = {s["status"] for s in result["steps"]}
    assert "dry_run" in statuses


def test_happy_path_create_poll_check(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    _install_built_after_create(monkeypatch, cms)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1", "--poll-interval", "1", "--timeout", "60"])
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["record_id"] == 500
    assert result["pipeline_status"] == "built"
    assert result["cost"]["demo"] == 9
    assert result["cost"]["voice_design"] == 0
    assert result["cost"]["clone"] == 0
    assert any(s["step"] == "check" and s["status"] == "ok" for s in result["steps"])
    # Default index=false (staged indexing); explicit --index true overrides.
    assert cms.last_create_fields["index"] is False


def test_index_true_overrides_default(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    _install_built_after_create(monkeypatch, cms)
    result = commands.voice_to_page(
        cfg, ["--voice-id", "v1", "--index", "true", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert cms.last_create_fields["index"] is True


def test_candidate_name_is_slugified_with_voice_id(monkeypatch):
    """task #28: candidate name/canonicalSlug must never contain spaces
    (Payload auto-slugs from name; "Ultron Voice AI" produced
    canonicalSlug="voice/ultron voice ai" and broke the URL)."""
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    _install_built_after_create(monkeypatch, cms)
    result = commands.voice_to_page(
        cfg, ["--voice-id", "v1", "--name", "Ultron Voice AI", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert cms.last_create_fields["name"] == "ultron-voice-ai-v1"
    assert cms.last_create_fields["canonicalSlug"] == "voice/ultron-voice-ai-v1"


def test_candidate_cjk_name_falls_back_to_voice_id(monkeypatch):
    """Non-ASCII names slugify to nothing; fall back to voiceId so the
    canonicalSlug stays valid."""
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    _install_built_after_create(monkeypatch, cms)
    result = commands.voice_to_page(
        cfg, ["--voice-id", "v1", "--name", "朗読家（明人）", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert cms.last_create_fields["name"] == "v1"
    assert cms.last_create_fields["canonicalSlug"] == "voice/v1"


def test_existing_built_skips_create(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    cms.records["v1"] = cms.records["42"] = {
        "id": 42,
        "voiceId": "v1",
        "pipelineStatus": "built",
        "pipelineStaging": {"assets": [{"slug": "a"}]},
    }
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1"])
    assert result["ok"] is True
    assert result["record_id"] == 42
    assert any(s["step"] == "candidate" and s["status"] == "skipped" for s in result["steps"])


def test_no_assets_needs_review(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    cms.records["v1"] = cms.records["42"] = {
        "id": 42,
        "voiceId": "v1",
        "pipelineStatus": "built",
        "pipelineStaging": {"assets": []},
    }
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1"])
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "no demo assets" in result["reason"]


def test_alert_hook_called_on_needs_review(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_ALERT_HOOK="https://alert.example")
    cms = FakeCms()
    cms.records["v1"] = cms.records["42"] = {
        "id": 42,
        "voiceId": "v1",
        "pipelineStatus": "built",
        "pipelineStaging": {"assets": []},
    }
    calls = []
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE, hook_calls=calls)
    result = commands.voice_to_page(cfg, ["--voice-id", "v1"])
    assert result["exit_code"] == 2
    assert any(payload.get("status") == "needs_review" for _, payload in calls)


def test_command_string_hook_uses_shlex(monkeypatch):
    import subprocess

    captured = {}

    def fake_run(argv, capture_output=False, text=False, timeout=30):
        captured["argv"] = argv
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    detail = commands._run_hook(
        "python /opt/convert_to_public.py --apply --voice-id", {"voice_id": "v1", "reason": "r"}
    )
    assert detail == "hook exit 0"
    assert captured["argv"] == [
        "python", "/opt/convert_to_public.py", "--apply", "--voice-id", "v1",
    ]


def test_url_hook_sends_bearer_token(monkeypatch):
    import urllib.request

    captured = {}

    def fake_urlopen(req, timeout=30):
        captured["headers"] = {k: v for k, v in req.header_items()}
        return type("R", (), {"status": 200, "__enter__": lambda s: s, "__exit__": lambda *a: False})()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    detail = commands._run_hook(
        "https://noiz.ai/api/v1/voices/v1/publicize",
        {"voice_id": "v1"},
        token="secret-token",
    )
    assert detail == "hook HTTP 200"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"


def test_url_hook_without_token_has_no_auth_header(monkeypatch):
    import urllib.request

    captured = {}

    def fake_urlopen(req, timeout=30):
        captured["headers"] = {k: v for k, v in req.header_items()}
        return type("R", (), {"status": 200, "__enter__": lambda s: s, "__exit__": lambda *a: False})()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    commands._run_hook("https://noiz.ai/api/v1/voices/v1/publicize", {"voice_id": "v1"})
    assert "Authorization" not in captured["headers"]


# --- B-tier (PRD v0.5.2) -----------------------------------------------------

B_DESC = "young male british calm authoritative narrator voice for anime trailers"


def test_b_requires_keyword(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(cfg, ["--character", "Gojo", "--source", "JJK", "--description", B_DESC])
    assert result["ok"] is False
    assert "--keyword" in result["error"]


def test_b_character_source_pairing(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(cfg, ["--keyword", "k", "--character", "Gojo", "--description", B_DESC])
    assert result["ok"] is False
    assert "together" in result["error"]


def test_b_description_length_hard_check(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(cfg, ["--keyword", "k", "--character", "Gojo", "--source", "JJK", "--description", "x"])
    assert result["ok"] is False
    assert "20-500" in result["error"]


def test_b_language_required_for_zh_keyword(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(
        cfg,
        ["--input", '{"keyword":"k","character":"c","source":"s","description":"%s","target_language":"zh"}' % B_DESC],
    )
    assert result["ok"] is False
    assert "--language is required" in result["error"]


def test_b_scene_prefill(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(
        cfg,
        ["--keyword", "k", "--character", "c", "--source", "s",
         "--description", B_DESC, "--scene", "wellness", "--dry-run"],
    )
    assert result["ok"] is True
    assert any(s["step"] == "scene_prefill" for s in result["steps"])
    assert result["b_input"]["gender"] == "female"
    assert result["b_input"]["age"] == "young"
    assert "wellness" in result["b_input"]["labels"]

    # adult is not a valid clone-api age alias; adult-scenes map to neutral
    # (PM 2026-08-10).
    result2 = commands.voice_to_page(
        cfg,
        ["--keyword", "k", "--character", "c", "--source", "s",
         "--description", B_DESC, "--scene", "podcast", "--dry-run"],
    )
    assert result2["ok"] is True
    assert result2["b_input"]["age"] == "neutral"


def test_b_dry_run_shows_description_draft(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/voice_design_clone.py")
    result = commands.voice_to_page(
        cfg, ["--keyword", "k", "--character", "c", "--source", "s", "--dry-run"]
    )
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "description_draft" in result
    assert any(s["step"] == "voice_create" and s["status"] == "dry_run" for s in result["steps"])


def test_b_missing_description_needs_review(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(
        cfg, ["--keyword", "k", "--character", "c", "--source", "s"]
    )
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "description" in result["reason"]


def test_b_voice_create_hook_then_a_flow(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/voice_design_clone.py --env test")
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=dict(PUBLIC_VOICE, voice_id="vB"))
    _install_built_after_create(monkeypatch, cms)
    captured = {}

    def fake_hook(hook, payload, timeout=120):
        captured["payload"] = payload
        return {"voice_id": "vB"}

    monkeypatch.setattr(commands, "_run_hook_json", fake_hook)
    result = commands.voice_to_page(
        cfg, ["--keyword", "k", "--character", "c", "--source", "s",
              "--description", B_DESC, "--timeout", "60"]
    )
    assert result["ok"] is True
    assert captured["payload"]["env"] == "test"
    assert captured["payload"]["character"] == "c"
    assert captured["payload"]["source"] == "s"
    assert captured["payload"]["description"] == B_DESC


def test_b_confirm_description_without_description(monkeypatch):
    """--confirm-description with no --description must not crash with
    UnboundLocalError; the generated draft is sent to the create hook."""
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/voice_design_clone.py --env test")
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=dict(PUBLIC_VOICE, voice_id="vB"))
    _install_built_after_create(monkeypatch, cms)
    captured = {}

    def fake_hook(hook, payload, timeout=120):
        captured["payload"] = payload
        return {"voice_id": "vB"}

    monkeypatch.setattr(commands, "_run_hook_json", fake_hook)
    result = commands.voice_to_page(
        cfg,
        ["--keyword", "k", "--character", "c", "--source", "s",
         "--confirm-description", "--timeout", "60"],
    )
    assert result["ok"] is True
    assert captured["payload"]["description"]
    assert any(s["step"] == "voice_create" and s["status"] == "ok" for s in result["steps"])
    assert any(s["step"] == "voice_create" and s["status"] == "ok" for s in result["steps"])
    assert result["voice_id"] == "vB"
    assert result["page_hint"]["slug_base"] == "k"
    assert result["b_input"]["keyword"] == "k"
    # B delegation must NOT force index=true; A default false applies.
    assert cms.last_create_fields["index"] is False


def test_b_index_true_override(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/voice_design_clone.py --env test")
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=dict(PUBLIC_VOICE, voice_id="vB"))
    _install_built_after_create(monkeypatch, cms)
    monkeypatch.setattr(commands, "_run_hook_json", lambda hook, payload, timeout=120: {"voice_id": "vB"})
    result = commands.voice_to_page(
        cfg, ["--keyword", "k", "--character", "c", "--source", "s",
              "--description", B_DESC, "--index", "true", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert cms.last_create_fields["index"] is True


def test_b_keyword_shortcut_uses_existing_voice(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    cms.records["vShort"] = cms.records["42"] = {
        "id": 42, "voiceId": "vShort", "pipelineStatus": "built",
        "pipelineStaging": {"assets": [{"slug": "a"}]},
    }
    _patch_env(monkeypatch, cms=cms, voice=dict(PUBLIC_VOICE, voice_id="vShort"))
    result = commands.voice_to_page(
        cfg,
        ["--input", '{"keyword":"k","character":"c","source":"s","description":"%s","voice_id":"vShort"}' % B_DESC],
    )
    assert result["ok"] is True
    assert any(s["step"] == "keyword_shortcut" for s in result["steps"])
    assert result["voice_id"] == "vShort"


def test_b_hook_missing_needs_review(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(
        cfg, ["--keyword", "k", "--character", "c", "--source", "s",
              "--description", B_DESC, "--confirm-description"]
    )
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "NOIZ_VOICE_CREATE_HOOK" in result["reason"]


def test_b_hook_needs_review_status_maps_to_exit_2(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/voice_design_clone.py --env test")
    _patch_env(monkeypatch, cms=FakeCms(), voice=PUBLIC_VOICE)
    monkeypatch.setattr(
        commands, "_run_hook_json",
        lambda hook, payload, timeout=120: {"status": "needs_review", "reason": "low preview score (3.2)"},
    )
    result = commands.voice_to_page(
        cfg, ["--keyword", "k", "--character", "c", "--source", "s",
              "--description", B_DESC, "--confirm-description"]
    )
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "low preview score" in result["reason"]


def test_run_hook_json_writes_input_file(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output=False, text=False, timeout=120):
        captured["argv"] = list(argv)
        with open(argv[argv.index("--input") + 1], encoding="utf-8") as fh:
            captured["payload"] = json.load(fh)
        return type("P", (), {"returncode": 0, "stdout": '{"voice_id":"vNew"}'})()

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    out = commands._run_hook_json(
        "python /tmp/voice_design_clone.py --env test",
        {"keyword": "k", "character": "c", "source": "s", "description": "d", "env": "test"},
    )
    assert out == {"voice_id": "vNew"}
    assert captured["argv"][:4] == ["python", "/tmp/voice_design_clone.py", "--env", "test"]
    assert captured["argv"][-2] == "--input"
    assert captured["payload"]["keyword"] == "k"
    assert captured["payload"]["env"] == "test"
    assert not os.path.exists(captured["argv"][-1])


def test_run_hook_json_parses_multiline_json(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output=False, text=False, timeout=600):
        captured["timeout"] = timeout
        return type(
            "P", (), {"returncode": 0,
                      "stdout": '{\n  "voice_id": "vMulti",\n  "status": "created"\n}'}
        )()

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    out = commands._run_hook_json("python /tmp/x.py", {"keyword": "k"})
    assert out == {"voice_id": "vMulti", "status": "created"}
    assert captured["timeout"] == 600  # default 600s for slow B-tier hooks


def test_run_hook_json_falls_back_to_last_line(monkeypatch):
    def fake_run(argv, capture_output=False, text=False, timeout=600):
        return type("P", (), {"returncode": 0, "stdout": 'log line\n{"voice_id":"vL"}'})()

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    out = commands._run_hook_json("python /tmp/x.py", {"keyword": "k"})
    assert out == {"voice_id": "vL"}


def test_run_hook_json_nonzero_exit_surfaces_diagnostics(monkeypatch):
    def fake_run(argv, capture_output=False, text=False, timeout=600):
        return type(
            "P", (),
            {"returncode": 1, "stdout": "", "stderr": "TTS submit failed: credit limit exceeded"},
        )()

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    out = commands._run_hook_json("python /tmp/audio_clone.py", {"ref_audio": "/tmp/x.mp3"})
    assert out["status"] == "needs_review"
    assert "credit limit exceeded" in out["reason"]
    assert "hook exit 1" in out["reason"]


# --- C-tier (PRD v0.6, M3) ----------------------------------------------------


def test_c_requires_ref_audio(monkeypatch):
    cfg = _cfg(monkeypatch)
    # JSON input without mode markers defaults to B-tier, which requires keyword.
    result = commands.voice_to_page(cfg, ["--input", '{"name":"x"}'])
    assert result["ok"] is False
    assert "--keyword" in result["error"]


def test_c_character_source_pairing(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(
        cfg, ["--ref-audio", "/tmp/sample.mp3", "--character", "Person"]
    )
    assert result["ok"] is False
    assert "together" in result["error"]


def test_c_dry_run_no_hook_call(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/audio_clone.py")
    calls = []

    def record_hook(hook, payload, timeout=600):
        calls.append(payload)
        return {"voice_id": "vC"}

    monkeypatch.setattr(commands, "_run_hook_json", record_hook)
    result = commands.voice_to_page(cfg, ["--ref-audio", "/tmp/sample.mp3", "--dry-run"])
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert calls == []
    assert any(s["step"] == "clone" and s["status"] == "dry_run" for s in result["steps"])


def test_c_hook_missing_needs_review(monkeypatch):
    cfg = _cfg(monkeypatch)
    result = commands.voice_to_page(cfg, ["--ref-audio", "/tmp/sample.mp3"])
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "NOIZ_VOICE_CREATE_HOOK" in result["reason"]


def test_c_hook_returns_voice_id_then_a_flow(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/audio_clone.py --env test")
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=dict(PUBLIC_VOICE, voice_id="vC"))
    _install_built_after_create(monkeypatch, cms)
    captured = {}

    def fake_hook(hook, payload, timeout=600):
        captured["payload"] = payload
        return {"voice_id": "vC"}

    monkeypatch.setattr(commands, "_run_hook_json", fake_hook)
    result = commands.voice_to_page(
        cfg, ["--ref-audio", "/tmp/sample.mp3", "--language", "ja", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert captured["payload"]["ref_audio"] == "/tmp/sample.mp3"
    assert captured["payload"]["language"] == "ja"
    assert captured["payload"]["env"] == "test"
    assert result["voice_id"] == "vC"
    assert result["page_hint"]["slug_base"] == "vC"
    assert result["c_input"]["ref_audio"] == "/tmp/sample.mp3"
    # C delegation also defaults to index=false.
    assert cms.last_create_fields["index"] is False


def test_c_hook_needs_review_maps_to_exit_2(monkeypatch):
    cfg = _cfg(monkeypatch, NOIZ_VOICE_CREATE_HOOK="python /tmp/audio_clone.py")
    _patch_env(monkeypatch, cms=FakeCms(), voice=PUBLIC_VOICE)
    monkeypatch.setattr(
        commands, "_run_hook_json",
        lambda hook, payload, timeout=600: {"status": "needs_review", "reason": "audio quality low"},
    )
    result = commands.voice_to_page(cfg, ["--ref-audio", "/tmp/sample.mp3"])
    assert result["ok"] is False
    assert result["exit_code"] == 2
    assert "audio quality low" in result["reason"]


def test_regen_existing_resets_record_and_polls(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    cms.records["1"] = {
        "id": 1,
        "voiceId": "v1",
        "pipelineStatus": "built",
        "pipelineStaging": {"assets": [{"slug": "a"}] * 9},
    }
    cms.records["v1"] = cms.records["1"]
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    result = commands.voice_to_page(
        cfg, ["--voice-id", "v1", "--regen-existing", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert any(
        s["step"] == "candidate" and "reset to pending_assets" in s["detail"]
        for s in result["steps"]
    )
    assert cms.patch_calls == [(1, {"pipelineStatus": "pending_assets"})]
    assert result["record_id"] == 1


def test_regen_existing_requires_existing_record(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    result = commands.voice_to_page(
        cfg, ["--voice-id", "ghost", "--regen-existing"]
    )
    assert result["ok"] is False
    assert "no existing record for ghost (--regen-existing)" in result["error"]


def test_seo_full_sets_rebuild_mode(monkeypatch):
    cfg = _cfg(monkeypatch)
    cms = FakeCms()
    cms.records["1"] = {
        "id": 1,
        "voiceId": "v1",
        "pipelineStatus": "built",
        "pipelineStaging": {"assets": [{"slug": "a"}] * 9},
    }
    cms.records["v1"] = cms.records["1"]
    _patch_env(monkeypatch, cms=cms, voice=PUBLIC_VOICE)
    result = commands.voice_to_page(
        cfg, ["--voice-id", "v1", "--seo-full", "--timeout", "60"]
    )
    assert result["ok"] is True
    assert result["rebuild_mode"] == "full"
    assert "seo-full" in result["rebuild_note"]
