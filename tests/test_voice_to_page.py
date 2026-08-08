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
        def record_hook(hook, payload, timeout=30):
            hook_calls.append((hook, payload))
            return "hook exit 0"
        monkeypatch.setattr(commands, "_run_hook", record_hook)
    return cms


def _install_built_after_create(monkeypatch, cms):
    def built_after_create(fields):
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
        "python /opt/convert_to_public.py --apply", {"voice_id": "v1", "reason": "r"}
    )
    assert detail == "hook exit 0"
    assert captured["argv"] == [
        "python", "/opt/convert_to_public.py", "--apply", "v1", "r",
    ]


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
    assert any(s["step"] == "voice_create" and s["status"] == "ok" for s in result["steps"])
    assert result["voice_id"] == "vB"
    assert result["page_hint"]["slug_base"] == "k"
    assert result["b_input"]["keyword"] == "k"


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
