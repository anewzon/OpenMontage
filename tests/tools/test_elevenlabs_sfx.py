"""elevenlabs_sfx against the current ElevenLabs Sound Effects contract.

No network: `requests.post` is replaced for every call, and the session guard
in tests/conftest.py would block a real connection anyway.
"""

from __future__ import annotations

import wave
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tools.audio import elevenlabs_sfx as sfx_module
from tools.audio.elevenlabs_sfx import ElevenLabsSFX
from tools.base_tool import ToolStatus
from tools.tool_registry import ToolRegistry

KEY = "xi-test-SECRET-456"
URL = "https://api.elevenlabs.io/v1/sound-generation"


def _resp(status: int = 200, content: bytes = b"ID3fake", json_data: Any = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.headers = {"request-id": "req-1"}
    r.json.return_value = json_data
    return r


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.delenv(sfx_module.PRICE_ENV, raising=False)


@pytest.fixture
def tool() -> ElevenLabsSFX:
    t = ElevenLabsSFX()
    t._probe_duration = staticmethod(lambda path: None)  # no ffprobe dependency
    return t


def _inputs(tmp_path, **extra) -> dict[str, Any]:
    return {
        "prompt": "continuous close-perspective bed matching the visible scene",
        "duration_seconds": 20,
        "loop": True,
        "prompt_influence": 0.45,
        "output_path": str(tmp_path / "sfx" / "bed_a.mp3"),
        **extra,
    }


def _run(tool, inputs, response):
    with patch("requests.post", return_value=response) as post:
        result = tool.execute(inputs)
    return result, post


# ---- identity -------------------------------------------------------------


def test_discovered_as_its_own_sfx_capability():
    registry = ToolRegistry()
    registry.discover()
    found = registry.get("elevenlabs_sfx")
    assert found is not None
    assert found.capability == "sfx_generation" and found.provider == "elevenlabs"
    assert [t.name for t in registry.get_by_capability("sfx_generation")] == ["elevenlabs_sfx"]
    music = [t.name for t in registry.get_by_capability("music_generation")]
    assert "elevenlabs_sfx" not in music, "SFX must not be offered as music"
    assert "sfx_generation" in registry.provider_menu()


def test_unavailable_without_a_credential(monkeypatch, tool, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    with patch("requests.post") as post:
        result = tool.execute(_inputs(tmp_path))
    assert result.success is False and "ELEVENLABS_API_KEY" in result.error
    post.assert_not_called()


# ---- request contract -------------------------------------------------------


def test_request_matches_the_documented_endpoint(keyed, tool, tmp_path):
    result, post = _run(tool, _inputs(tmp_path), _resp())
    assert result.success is True, result.error
    assert post.call_count == 1
    args, kwargs = post.call_args
    assert args[0] == URL
    assert kwargs["headers"]["xi-api-key"] == KEY
    assert kwargs["params"] == {"output_format": "mp3_44100_128"}
    assert kwargs["json"] == {
        "text": "continuous close-perspective bed matching the visible scene",
        "model_id": "eleven_text_to_sound_v2",
        "duration_seconds": 20.0,
        "loop": True,
        "prompt_influence": 0.45,
    }


def test_duration_can_be_left_to_the_model(keyed, tool, tmp_path):
    result, post = _run(tool, _inputs(tmp_path, duration_seconds=None), _resp())
    assert "duration_seconds" not in post.call_args.kwargs["json"]
    assert result.data["cost_basis"] == "maximum_duration_assumed"


@pytest.mark.parametrize("duration", [0.4, 30.5, -1])
def test_duration_bounds(keyed, tool, tmp_path, duration):
    result, post = _run(tool, _inputs(tmp_path, duration_seconds=duration), _resp())
    assert result.success is False and "duration_seconds" in result.error
    post.assert_not_called()


@pytest.mark.parametrize("duration", [0.5, 30])
def test_duration_bounds_are_inclusive(keyed, tool, tmp_path, duration):
    result, _ = _run(tool, _inputs(tmp_path, duration_seconds=duration), _resp())
    assert result.success is True


def test_prompt_influence_bounds(keyed, tool, tmp_path):
    result, post = _run(tool, _inputs(tmp_path, prompt_influence=1.2), _resp())
    assert result.success is False
    post.assert_not_called()


def test_only_the_current_model_is_accepted(keyed, tool, tmp_path):
    assert sfx_module.MODEL_IDS == ("eleven_text_to_sound_v2",)
    result, post = _run(tool, _inputs(tmp_path, model_id="eleven_text_to_sound_v1"), _resp())
    assert result.success is False
    post.assert_not_called()


def test_output_format_is_validated_and_sent(keyed, tool, tmp_path):
    bad, post = _run(tool, _inputs(tmp_path, output_format="flac_48000"), _resp())
    assert bad.success is False
    post.assert_not_called()
    ok, post = _run(tool, _inputs(tmp_path, output_format="opus_48000_128"), _resp())
    assert ok.success is True
    assert post.call_args.kwargs["params"] == {"output_format": "opus_48000_128"}


def test_pcm_output_is_wrapped_as_a_real_wav(keyed, tool, tmp_path):
    pcm = b"\x00\x00" * 48000  # one second of 16-bit mono silence
    result, _ = _run(tool, _inputs(tmp_path, output_format="pcm_48000"), _resp(content=pcm))
    path = result.data["output"]
    assert path.endswith(".wav")
    with wave.open(path) as w:
        assert w.getframerate() == 48000 and w.getnchannels() == 1 and w.getnframes() == 48000


def test_output_stays_inside_the_project(keyed, tool, tmp_path):
    result, _ = _run(tool, _inputs(tmp_path), _resp())
    assert result.data["output"].startswith(str(tmp_path))
    assert (tmp_path / "sfx" / "bed_a.mp3").read_bytes() == b"ID3fake"


def test_output_path_is_required(keyed, tool):
    with patch("requests.post") as post:
        result = tool.execute({"prompt": "x"})
    assert result.success is False and "output_path" in result.error
    post.assert_not_called()


# ---- pricing ----------------------------------------------------------------


def test_pricing_follows_the_published_per_minute_rate(keyed, tool):
    assert sfx_module.USD_PER_MINUTE == 0.12
    assert tool.estimate_cost({"duration_seconds": 30}) == 0.06
    assert tool.estimate_cost({"duration_seconds": 10}) == 0.02
    assert tool.estimate_cost({}) == 0.06, "unspecified duration is priced at the 30 s maximum"


def test_price_override_is_isolated_and_validated(monkeypatch, tool):
    monkeypatch.setenv(sfx_module.PRICE_ENV, "0.06")
    assert tool.estimate_cost({"duration_seconds": 30}) == 0.03
    monkeypatch.setenv(sfx_module.PRICE_ENV, "zero")
    with pytest.raises(ValueError):
        tool.estimate_cost({"duration_seconds": 30})


def test_actual_cost_is_reported(keyed, tool, tmp_path):
    result, _ = _run(tool, _inputs(tmp_path), _resp())
    assert result.cost_usd == 0.04
    assert result.data["charge_status"] == "charged"
    assert result.model == "elevenlabs/eleven_text_to_sound_v2"


def test_auto_duration_is_reconciled_from_the_delivered_file(keyed, tmp_path):
    t = ElevenLabsSFX()
    t._probe_duration = staticmethod(lambda path: 12.0)
    result, _ = _run(t, _inputs(tmp_path, duration_seconds=None), _resp())
    assert result.cost_usd == 0.024
    assert result.data["cost_basis"] == "measured_output_duration"


# ---- failures ---------------------------------------------------------------


def test_authentication_failure(keyed, tool, tmp_path):
    result, _ = _run(tool, _inputs(tmp_path), _resp(401, b"", {"detail": {"status": "invalid_api_key"}}))
    assert result.success is False and "authentication failed" in result.error
    assert result.cost_usd == 0.0


def test_rate_limiting(keyed, tool, tmp_path):
    result, post = _run(tool, _inputs(tmp_path), _resp(429, b"", {"detail": {"message": "busy"}}))
    assert result.success is False and "rate limited" in result.error
    assert post.call_count == 1, "the tool never retries a paid call itself"
    assert result.cost_usd == 0.0


def test_validation_error_detail_is_surfaced(keyed, tool, tmp_path):
    body = {"detail": [{"loc": ["body", "text"], "msg": "field required", "type": "missing"}]}
    result, _ = _run(tool, _inputs(tmp_path), _resp(422, b"", body))
    assert result.success is False and "field required" in result.error


def test_transport_failure_is_an_unknown_charge(keyed, tool, tmp_path):
    import requests

    with patch("requests.post", side_effect=requests.Timeout("slow")):
        result = tool.execute(_inputs(tmp_path))
    assert result.success is False
    assert result.data["charge_status"] == "unknown"
    assert result.cost_usd == 0.04


def test_secret_never_leaks(keyed, tool, tmp_path):
    result, _ = _run(tool, _inputs(tmp_path), _resp(401, b"", {"detail": f"bad key {KEY}"}))
    assert KEY not in result.error and KEY not in repr(result.data)
    ok, _ = _run(tool, _inputs(tmp_path), _resp())
    assert KEY not in repr(ok.data)


def test_dry_run_never_calls_the_api(keyed, tool, tmp_path):
    with patch("requests.post") as post:
        report = tool.dry_run(_inputs(tmp_path))
    post.assert_not_called()
    assert report["estimated_cost_usd"] == 0.04 and report["would_execute"] is True


def test_tool_holds_no_environment_taxonomy():
    """The tool is provider plumbing; which sounds to make is the caller's call."""
    from pathlib import Path

    src = Path(sfx_module.__file__).read_text(encoding="utf-8").lower()
    for word in ("water", "river", "forest", "bird", "rain", "fire", "ocean", "wind"):
        assert word not in src, f"elevenlabs_sfx names a sound category ({word!r})"


# ---- existing-file protection (Phase 1) -----------------------------------


@pytest.mark.parametrize("fmt,name", [(None, "bed_a.mp3"), ("pcm_44100", "bed_a.wav")])
def test_an_existing_paid_file_is_never_overwritten(keyed, tool, tmp_path, fmt, name):
    sfx = tmp_path / "sfx"
    sfx.mkdir()
    (sfx / name).write_bytes(b"paid earlier")
    extra = {"output_format": fmt} if fmt else {}
    result, post = _run(tool, _inputs(tmp_path, **extra), _resp())
    assert result.success is False and "overwrite" in result.error
    assert result.data["charge_status"] == "not_charged"
    post.assert_not_called()
    assert (sfx / name).read_bytes() == b"paid earlier"


def test_overwriting_an_sfx_file_is_an_explicit_choice(keyed, tool, tmp_path):
    (tmp_path / "sfx").mkdir()
    (tmp_path / "sfx" / "bed_a.mp3").write_bytes(b"paid earlier")
    result, post = _run(tool, _inputs(tmp_path, overwrite=True), _resp())
    assert result.success is True and post.call_count == 1
