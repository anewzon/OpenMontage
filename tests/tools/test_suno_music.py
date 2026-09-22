"""suno_music against the live sunoapi.org contract - with no network at all.

Every provider response here is a fake shaped like the documentation read on
2026-09-23. The session network guard (tests/conftest.py) would block a real
call anyway; these tests never attempt one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tools.audio import suno_music as suno_module
from tools.audio.suno_music import (
    CURRENT_MODELS,
    DEPRECATED_MODELS,
    SunoMusic,
    SunoPricingUnconfirmed,
)
from tools.base_tool import ToolStatus
from tools.tool_registry import ToolRegistry

KEY = "sk-suno-test-SECRET-123"
BASE = "https://api.sunoapi.org/api/v1"


def _resp(json_data: Any = None, *, status: int = 200, content: bytes = b"") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.content = content
    r.headers = {}
    if status >= 400:
        import requests

        r.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    else:
        r.raise_for_status.return_value = None
    return r


def _track(i: int, duration: float = 131.2) -> dict[str, Any]:
    return {
        "id": f"audio-{i}", "audio_url": f"https://cdn.example/{i}.mp3",
        "title": f"Take {i}", "tags": "calm, instrumental", "model_name": "chirp-v6",
        "duration": duration,
    }


class FakeSuno:
    """Routes requests.get / requests.post by URL, and counts every call."""

    def __init__(self, *, statuses=("PENDING", "FIRST_SUCCESS", "SUCCESS"),
                 tracks=None, submit=None, credits=(100, 88), shape="sunoData"):
        self.statuses = list(statuses)
        self.tracks = [_track(0), _track(1)] if tracks is None else tracks
        self.submit = submit or _resp({"code": 200, "msg": "success", "data": {"taskId": "task-1"}})
        self.credits = list(credits)
        self.shape = shape
        self.posts: list[dict[str, Any]] = []
        self.gets: list[str] = []

    def post(self, url, headers=None, json=None, timeout=None, **_):
        self.posts.append({"url": url, "headers": headers, "json": json})
        if isinstance(self.submit, Exception):
            raise self.submit
        return self.submit

    def get(self, url, params=None, headers=None, timeout=None, **_):
        self.gets.append(url)
        if url.endswith("/generate/credit"):
            value = self.credits.pop(0) if self.credits else None
            return _resp({"code": 200, "msg": "success", "data": value})
        if url.endswith("/generate/record-info"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            response = {"taskId": "task-1", self.shape: self.tracks}
            data = {"taskId": "task-1", "status": status, "response": response,
                    "errorCode": None, "errorMessage": None}
            if status in ("GENERATE_AUDIO_FAILED", "SENSITIVE_WORD_ERROR"):
                data["errorMessage"] = "provider said no"
            return _resp({"code": 200, "msg": "success", "data": data})
        if url.startswith("https://cdn.example/"):
            return _resp(content=b"ID3-fake-" + url.encode())
        raise AssertionError(f"unexpected GET {url}")


@pytest.fixture
def tool() -> SunoMusic:
    t = SunoMusic()
    t._POLL_INTERVAL = 0
    t._BACKOFF = 0
    return t


@pytest.fixture(autouse=True)
def clean_pricing(monkeypatch):
    """No test inherits a price override or a recorded mismatch."""
    for model in CURRENT_MODELS + DEPRECATED_MODELS:
        monkeypatch.delenv(f"SUNO_CREDITS_PER_GENERATION_{model}", raising=False)
    monkeypatch.delenv("SUNO_CREDITS_PER_GENERATION", raising=False)
    suno_module._PRICING_MISMATCHES.clear()
    yield
    suno_module._PRICING_MISMATCHES.clear()


@pytest.fixture
def priced(monkeypatch):
    """A configured key. V6 needs no price setting: it is calibrated."""
    monkeypatch.setenv("SUNO_API_KEY", KEY)


def _instrumental(tmp_path, **extra) -> dict[str, Any]:
    return {
        "prompt": "slow felt piano, soft pads",
        "style": "slow felt piano, warm ambient pads, calm",
        "custom_mode": True,
        "instrumental": True,
        "model": "V6",
        "duration_seconds": 130,
        "output_path": str(tmp_path / "music" / "take.mp3"),
        **extra,
    }


def _run(tool, inputs, fake):
    with patch("requests.post", side_effect=fake.post), patch("requests.get", side_effect=fake.get):
        return tool.execute(inputs)


# ---- identity & availability -------------------------------------------


def test_registers_as_the_existing_music_generation_tool():
    registry = ToolRegistry()
    registry.discover()
    found = registry.get("suno_music")
    assert found is not None
    assert found.capability == "music_generation"
    assert found.provider == "suno"
    assert "suno_music" in [t.name for t in registry.get_by_capability("music_generation")]


def test_unavailable_without_a_credential(monkeypatch, tool):
    monkeypatch.delenv("SUNO_API_KEY", raising=False)
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    result = tool.execute({"prompt": "x", "output_path": "x.mp3"})
    assert result.success is False and "SUNO_API_KEY" in result.error


def test_available_with_a_credential(monkeypatch, tool):
    monkeypatch.setenv("SUNO_API_KEY", KEY)
    assert tool.get_status() == ToolStatus.AVAILABLE


# ---- models -------------------------------------------------------------


def test_current_models_are_the_confirmed_v6_family():
    assert CURRENT_MODELS == ("V6", "V6_WILD", "V6_MINI")
    assert suno_module.DEFAULT_MODEL == "V6"
    enum = SunoMusic.input_schema["properties"]["model"]["enum"]
    for model in CURRENT_MODELS + DEPRECATED_MODELS:
        assert model in enum


@pytest.mark.parametrize("model", ["V6", "V6_WILD", "V6_MINI"])
def test_v6_family_payload_is_accepted(tool, model, tmp_path):
    payload = tool._build_payload(_instrumental(tmp_path, model=model))
    assert payload["model"] == model
    assert payload["duration"] == 130.0


@pytest.mark.parametrize("model", ["V4", "V4_5", "V5"])
def test_previously_supported_models_still_build(tool, model, tmp_path):
    payload = tool._build_payload({"prompt": "calm", "model": model})
    assert payload["model"] == model


def test_invented_model_identifier_is_rejected(tool):
    with pytest.raises(ValueError):
        tool._build_payload({"prompt": "calm", "model": "V6_5"})


def test_duration_rejected_for_a_model_that_ignores_it(tool, tmp_path):
    with pytest.raises(ValueError):
        tool._build_payload(_instrumental(tmp_path, model="V4_5"))


# ---- request construction ------------------------------------------------


def test_instrumental_custom_request_is_a_no_vocal_production_request(tool, tmp_path):
    payload = tool._build_payload(_instrumental(tmp_path, negative_tags="drums"))
    assert payload["customMode"] is True
    assert payload["instrumental"] is True
    assert "prompt" not in payload, "an instrumental must not send the prompt as lyrics"
    assert payload["style"].startswith("slow felt piano")
    tags = payload["negativeTags"].lower()
    assert "drums" in tags and "vocals" in tags and "singing" in tags
    assert payload["callBackUrl"].startswith("https://")


def test_custom_instrumental_without_style_uses_the_prompt_as_style(tool):
    payload = tool._build_payload({"prompt": "gentle ambient", "custom_mode": True,
                                   "instrumental": True})
    assert payload["style"] == "gentle ambient"


def test_custom_vocal_request_sends_lyrics(tool):
    payload = tool._build_payload({"prompt": "[Verse] la la", "custom_mode": True,
                                   "instrumental": False, "style": "folk"})
    assert payload["prompt"] == "[Verse] la la"
    assert "vocals" not in payload.get("negativeTags", "")


def test_non_custom_request_sends_the_description_only(tool):
    payload = tool._build_payload({"prompt": "calm piano", "instrumental": True})
    assert payload == {
        "model": "V6", "customMode": False, "instrumental": True,
        "callBackUrl": payload["callBackUrl"], "prompt": "calm piano",
    }


def test_non_custom_mode_rejects_custom_only_fields(tool):
    with pytest.raises(ValueError):
        tool._build_payload({"prompt": "calm", "duration_seconds": 60})


def test_duration_bounds_follow_the_provider(tool, tmp_path):
    for bad in (9, 361):
        with pytest.raises(ValueError):
            tool._build_payload(_instrumental(tmp_path, duration_seconds=bad))


# ---- pricing ----------------------------------------------------------------


def test_v6_is_priced_from_the_live_calibration_without_any_setting(tool):
    assert suno_module.VERIFIED_CREDITS_PER_GENERATION == {"V6": 12.0}
    assert suno_module.USD_PER_CREDIT == 0.005
    assert tool.estimate_cost({"prompt": "x"}) == 0.06
    assert tool.estimate_cost({"prompt": "x", "model": "V6"}) == 0.06
    assert tool.estimate_cost({"prompt": "x", "operation": "credits"}) == 0.0
    status = tool.pricing_status("V6")
    assert status["confirmed"] is True and status["source"] == "verified_calibration"


@pytest.mark.parametrize("model", ["V6_WILD", "V6_MINI", *DEPRECATED_MODELS])
def test_unverified_models_are_not_inferred_from_v6(tool, model):
    with pytest.raises(SunoPricingUnconfirmed) as exc:
        tool.estimate_cost({"prompt": "x", "model": model})
    assert "not inferred from another model" in str(exc.value)
    assert f"SUNO_CREDITS_PER_GENERATION_{model}" in str(exc.value)
    assert tool.pricing_status(model)["confirmed"] is False


def test_unpriced_generation_never_reaches_the_provider(priced, tool, tmp_path):
    fake = FakeSuno()
    result = _run(tool, _instrumental(tmp_path, model="V6_MINI"), fake)
    assert result.success is False
    assert fake.posts == [] and fake.gets == []
    assert result.cost_usd == 0.0
    dry = tool.dry_run(_instrumental(tmp_path, model="V6_WILD"))
    assert dry["would_execute"] is False and "V6_WILD" in dry["blocker"]


def test_the_old_general_setting_no_longer_prices_anything(monkeypatch, tool):
    monkeypatch.setenv("SUNO_CREDITS_PER_GENERATION", "99")
    assert tool.estimate_cost({"prompt": "x", "model": "V6"}) == 0.06
    with pytest.raises(SunoPricingUnconfirmed):
        tool.estimate_cost({"prompt": "x", "model": "V6_MINI"})


def test_per_model_override_is_an_optional_emergency_setting(monkeypatch, tool):
    monkeypatch.setenv("SUNO_CREDITS_PER_GENERATION_V6", "14")
    assert tool.estimate_cost({"prompt": "x", "model": "V6"}) == 0.07
    assert tool.pricing_status("V6")["source"] == "override:SUNO_CREDITS_PER_GENERATION_V6"
    monkeypatch.setenv("SUNO_CREDITS_PER_GENERATION_V6_MINI", "6")
    assert tool.estimate_cost({"prompt": "x", "model": "V6_MINI"}) == 0.03


def test_invalid_override_is_rejected(monkeypatch, tool):
    monkeypatch.setenv("SUNO_CREDITS_PER_GENERATION_V6", "abc")
    with pytest.raises(SunoPricingUnconfirmed):
        tool.estimate_cost({"prompt": "x"})


# ---- execution ------------------------------------------------------------


def test_one_paid_generation_keeps_every_candidate(priced, tool, tmp_path):
    fake = FakeSuno()
    result = _run(tool, _instrumental(tmp_path), fake)

    assert result.success is True, result.error
    assert len(fake.posts) == 1, "exactly one paid request"
    assert fake.posts[0]["url"] == f"{BASE}/generate"
    body = fake.posts[0]["json"]
    assert body["instrumental"] is True and body["customMode"] is True and body["model"] == "V6"

    candidates = result.data["candidates"]
    assert len(candidates) == 2 and all(c["downloaded"] for c in candidates)
    paths = [c["path"] for c in candidates]
    assert paths[0] == str((tmp_path / "music" / "take.mp3").resolve())
    assert paths[1].endswith("take__cand1.mp3")
    for p in paths:
        assert str(tmp_path) in p
    assert result.data["selection_status"] == "unreviewed"
    assert result.data["provider"] == "suno" and result.data["model"] == "V6"
    assert result.model == "suno/V6"
    assert result.data["duration_seconds"] == 131.2
    assert sorted(result.artifacts) == sorted(paths)


def test_track_index_chooses_which_candidate_lands_on_output_path(priced, tool, tmp_path):
    result = _run(tool, _instrumental(tmp_path, track_index=1), FakeSuno())
    primary = next(c for c in result.data["candidates"] if c["written_to_output_path"])
    assert primary["index"] == 1
    assert result.data["track_id"] == "audio-1"


def test_actual_cost_comes_from_the_credit_balance(priced, tool, tmp_path):
    result = _run(tool, _instrumental(tmp_path), FakeSuno(credits=(100, 88)))
    assert result.data["credits_consumed"] == 12
    assert result.cost_usd == 0.06
    assert result.data["cost_basis"] == "measured_credit_delta"
    assert result.data["pricing_check"]["status"] == "match"
    assert "pricing_mismatch" not in result.data and result.error is None


@pytest.mark.parametrize("after", [90, 80])
def test_an_off_rate_charge_is_surfaced_and_blocks_further_generation(priced, tool, tmp_path, after):
    result = _run(tool, _instrumental(tmp_path), FakeSuno(credits=(100, after)))
    assert result.success is True, "the paid candidates are still delivered"
    mismatch = result.data["pricing_mismatch"]
    assert mismatch["expected_credits"] == 12 and mismatch["measured_credits"] == 100 - after
    assert "PRICING MISMATCH" in result.error
    assert result.cost_usd == round((100 - after) * 0.005, 4), "the real charge is what is recorded"

    blocked = FakeSuno()
    again = _run(tool, _instrumental(tmp_path), blocked)
    assert again.success is False and "pricing mismatch" in again.error.lower()
    assert blocked.posts == [], "no paid call proceeds blindly after a mismatch"
    assert tool.pricing_status("V6")["mismatch"]["measured_credits"] == 100 - after

    suno_module.resolve_pricing_mismatch("V6")
    assert _run(tool, _instrumental(tmp_path), FakeSuno()).success is True


def test_a_failed_task_that_charged_off_rate_is_also_a_mismatch(priced, tool, tmp_path):
    fake = FakeSuno(statuses=("GENERATE_AUDIO_FAILED",), credits=(100, 95))
    result = _run(tool, _instrumental(tmp_path), fake)
    assert result.success is False
    assert result.data["pricing_mismatch"]["measured_credits"] == 5
    with pytest.raises(suno_module.SunoPricingMismatch):
        tool.estimate_cost({"prompt": "x"})


def test_an_unmeasurable_charge_is_reported_unverified_not_blocked(priced, tool, tmp_path):
    result = _run(tool, _instrumental(tmp_path), FakeSuno(credits=()))
    assert result.data["pricing_check"]["status"] == "unverified"
    assert "pricing_mismatch" not in result.data
    assert tool.estimate_cost({"prompt": "x"}) == 0.06


def test_falls_back_to_the_confirmed_estimate_without_a_balance(priced, tool, tmp_path):
    result = _run(tool, _instrumental(tmp_path), FakeSuno(credits=()))
    assert result.cost_usd == 0.06
    assert result.data["cost_basis"] == "confirmed_estimate"


def test_legacy_response_data_shape_is_parsed(priced, tool, tmp_path):
    result = _run(tool, _instrumental(tmp_path), FakeSuno(shape="data"))
    assert result.success is True and len(result.data["candidates"]) == 2


def test_insufficient_credits_fails_cleanly_and_uncharged(priced, tool, tmp_path):
    fake = FakeSuno(submit=_resp({"code": 429, "msg": "credits insufficient", "data": None}))
    result = _run(tool, _instrumental(tmp_path), fake)
    assert result.success is False
    assert "insufficient credits" in result.error
    assert result.data["charge_status"] == "not_charged"
    assert result.cost_usd == 0.0
    assert not any("record-info" in url for url in fake.gets)


def test_authentication_failure_is_reported(priced, tool, tmp_path):
    result = _run(tool, _instrumental(tmp_path), FakeSuno(submit=_resp(None, status=401)))
    assert result.success is False and "unauthorized" in result.error
    assert result.cost_usd == 0.0


def test_failed_task_is_costed_conservatively(priced, tool, tmp_path):
    fake = FakeSuno(statuses=("PENDING", "GENERATE_AUDIO_FAILED"), credits=())
    result = _run(tool, _instrumental(tmp_path), fake)
    assert result.success is False
    assert "GENERATE_AUDIO_FAILED" in result.error
    assert result.cost_usd == 0.06, "an unknown charge is costed at the estimate, not zero"


def test_failed_task_with_unchanged_balance_is_not_charged(priced, tool, tmp_path):
    fake = FakeSuno(statuses=("GENERATE_AUDIO_FAILED",), credits=(100, 100))
    result = _run(tool, _instrumental(tmp_path), fake)
    assert result.success is False
    assert result.cost_usd == 0.0 and result.data["charge_status"] == "not_charged"


def test_polling_is_bounded_and_points_to_a_free_recovery(priced, tool, tmp_path):
    fake = FakeSuno(statuses=("PENDING",), credits=())
    result = _run(tool, _instrumental(tmp_path, max_wait_seconds=3), fake)
    assert result.success is False
    assert "operation=fetch" in result.error and "task-1" in result.error
    assert sum("record-info" in u for u in fake.gets) == 4
    assert result.cost_usd == 0.06
    assert len(fake.posts) == 1, "the paid request is never retried by the tool"


def test_fetch_recovers_an_existing_task_without_paying(priced, tool, tmp_path):
    fake = FakeSuno(statuses=("SUCCESS",))
    result = _run(tool, {"operation": "fetch", "task_id": "task-1", "prompt": "",
                         "output_path": str(tmp_path / "music" / "take.mp3")}, fake)
    assert result.success is True
    assert fake.posts == []
    assert result.cost_usd == 0.0
    assert len(result.data["candidates"]) == 2


def test_remaining_credits_preflight_is_free(priced, tool):
    fake = FakeSuno(credits=(1234,))
    result = _run(tool, {"operation": "credits", "prompt": ""}, fake)
    assert result.success is True
    assert result.data["credits"] == 1234 and result.data["usd_value"] == 6.17
    assert result.cost_usd == 0.0 and fake.posts == []


def test_output_path_is_required(priced, tool):
    fake = FakeSuno()
    result = _run(tool, {"prompt": "calm", "custom_mode": False}, fake)
    assert result.success is False and "output_path" in result.error
    assert fake.posts == []


def test_secret_never_appears_in_errors_or_results(priced, tool, tmp_path):
    fake = FakeSuno(submit=RuntimeError(f"socket exploded near Bearer {KEY}"))
    result = _run(tool, _instrumental(tmp_path), fake)
    assert result.success is False
    assert KEY not in (result.error or "")
    assert KEY not in repr(result.data)
    assert "[REDACTED]" in result.error

    ok = _run(tool, _instrumental(tmp_path), FakeSuno())
    assert KEY not in repr(ok.data)
    assert fake.posts[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_request_exception_on_submit_is_an_unknown_charge(priced, tool, tmp_path):
    import requests

    fake = FakeSuno(submit=requests.ConnectionError("reset"), credits=())
    result = _run(tool, _instrumental(tmp_path), fake)
    assert result.success is False
    assert result.data["charge_status"] == "unknown"
    assert result.cost_usd == 0.06
    assert len(fake.posts) == 1
