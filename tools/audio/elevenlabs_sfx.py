"""Sound effects (SFX) generation via the ElevenLabs Sound Effects API.

A plain provider tool: text in, one audio file out. It knows nothing about any
channel, scene or environment — what to generate is decided by the calling
pipeline from its channel brief and the actual footage.

Contract verified against the live ElevenLabs API reference on 2026-09-23
(``/docs/api-reference/text-to-sound-effects/convert``):

- ``POST https://api.elevenlabs.io/v1/sound-generation``, header ``xi-api-key``
- body: ``text`` (required), ``duration_seconds`` (0.5-30, or null for the
  model to choose), ``prompt_influence`` (0-1, default 0.3), ``loop``
  (default false; only on ``eleven_text_to_sound_v2``), ``model_id``
  (only ``eleven_text_to_sound_v2`` is listed)
- ``output_format`` is a query parameter; PCM is returned headerless
- errors: 401 authentication, 422 validation, 429 rate limit

Pricing (ElevenAPI pricing page, same date): Sound Effects are billed per
generation at **$0.12 per minute** of generated audio on every tier. When
``duration_seconds`` is left to the model the length is unknown in advance, so
the estimate uses the 30-second maximum and the actual cost is reconciled from
the delivered file.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

MODEL_IDS = ("eleven_text_to_sound_v2",)
DEFAULT_MODEL = "eleven_text_to_sound_v2"
LOOP_MODELS = frozenset({"eleven_text_to_sound_v2"})
DURATION_RANGE_SECONDS = (0.5, 30.0)
PROMPT_INFLUENCE_DEFAULT = 0.3
OUTPUT_FORMATS = (
    "mp3_22050_32", "mp3_24000_48", "mp3_44100_32", "mp3_44100_64",
    "mp3_44100_96", "mp3_44100_128", "mp3_44100_192",
    "pcm_8000", "pcm_16000", "pcm_22050", "pcm_24000", "pcm_32000",
    "pcm_44100", "pcm_48000", "ulaw_8000", "alaw_8000",
    "opus_48000_32", "opus_48000_64", "opus_48000_96", "opus_48000_128",
    "opus_48000_192",
)
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

# ---- Pricing (kept together so it is easy to update) ----------------------
#: ElevenAPI list price for Sound Effects, all tiers, checked 2026-09-23.
USD_PER_MINUTE = 0.12
#: Optional operator override when the account is billed differently.
PRICE_ENV = "ELEVENLABS_SFX_USD_PER_MINUTE"


def usd_per_minute() -> float:
    raw = os.environ.get(PRICE_ENV, "").strip()
    if not raw:
        return USD_PER_MINUTE
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{PRICE_ENV} must be a finite number greater than 0") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{PRICE_ENV} must be a finite number greater than 0")
    return value


class ElevenLabsSFX(BaseTool):
    name = "elevenlabs_sfx"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    # Its own capability family: provider_menu()/get_by_capability() list SFX
    # providers without presenting them as music.
    capability = "sfx_generation"
    provider = "elevenlabs"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:ELEVENLABS_API_KEY"]
    install_instructions = (
        "Set ELEVENLABS_API_KEY in .env (key from "
        "https://elevenlabs.io/app/settings/api-keys)."
    )

    agent_skills = ["sound-effects", "elevenlabs"]

    capabilities = [
        "generate_sfx",
        "generate_loopable_sfx",
        "generate_ambience",
    ]
    supports = {
        "loop": True,
        "duration_control": True,
        "prompt_influence": True,
        "output_formats": True,
    }
    best_for = [
        "episode-specific environmental beds generated as short loopable sources",
        "one-shot detail sounds described in natural language",
        "sound that no recording in the project already provides",
    ]
    not_good_for = [
        "music (use a music_generation tool)",
        "speech or narration (use a tts tool)",
        "a single generation longer than 30 seconds (build long beds from loops)",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Natural-language description of the sound (sent as `text`).",
            },
            "duration_seconds": {
                "type": ["number", "null"],
                "minimum": DURATION_RANGE_SECONDS[0],
                "maximum": DURATION_RANGE_SECONDS[1],
                "description": (
                    "0.5-30 s. Omit/null to let the model choose; the estimate "
                    "then assumes the 30 s maximum."
                ),
            },
            "loop": {
                "type": "boolean",
                "default": False,
                "description": "Seamlessly loopable output (eleven_text_to_sound_v2 only).",
            },
            "prompt_influence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": PROMPT_INFLUENCE_DEFAULT,
            },
            "model_id": {"type": "string", "enum": list(MODEL_IDS), "default": DEFAULT_MODEL},
            "output_format": {
                "type": "string",
                "enum": list(OUTPUT_FORMATS),
                "default": DEFAULT_OUTPUT_FORMAT,
                "description": (
                    "codec_samplerate_bitrate. mp3_44100_192 needs Creator tier "
                    "or above, pcm_44100 needs Pro. PCM is wrapped as WAV."
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Explicit path inside the project workspace.",
            },
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": (
                    "The request is refused, before anything is paid for, when "
                    "the output file already exists. Set true only to replace "
                    "it deliberately."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=20, network_required=True
    )
    # Every request is a paid generation, so the tool never retries it itself.
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = [
        "prompt", "duration_seconds", "loop", "prompt_influence", "model_id", "output_format",
    ]
    side_effects = ["writes one audio file to output_path", "calls the ElevenLabs Sound Effects API"]
    user_visible_verification = [
        "Listen for fit with the picture, artefacts, and (for loops) an audible seam",
    ]

    _URL = "https://api.elevenlabs.io/v1/sound-generation"

    def _get_api_key(self) -> Optional[str]:
        return os.environ.get("ELEVENLABS_API_KEY") or None

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["pricing"] = {
            "usd_per_minute": usd_per_minute(),
            "billing": "per generation, by generated duration",
            "unspecified_duration_estimate_seconds": DURATION_RANGE_SECONDS[1],
            "override_env": PRICE_ENV,
        }
        info["limits"] = {
            "duration_seconds": list(DURATION_RANGE_SECONDS),
            "loop_models": sorted(LOOP_MODELS),
        }
        return info

    # ---- pricing ----------------------------------------------------------

    @staticmethod
    def billable_seconds(inputs: dict[str, Any]) -> float:
        """Seconds the estimate charges for: the request, or the 30 s maximum."""
        duration = inputs.get("duration_seconds")
        return float(duration) if duration is not None else DURATION_RANGE_SECONDS[1]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(self.billable_seconds(inputs) / 60.0 * usd_per_minute(), 4)

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"tool": self.name, "status": self.get_status().value}
        try:
            body, params = self._build_request(inputs)
            result.update(
                request_body=body,
                query=params,
                estimated_cost_usd=self.estimate_cost(inputs),
                would_execute=result["status"] == ToolStatus.AVAILABLE.value,
            )
        except ValueError as exc:
            result.update(estimated_cost_usd=None, would_execute=False, blocker=str(exc))
        return result

    # ---- request ----------------------------------------------------------

    @staticmethod
    def _build_request(inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        text = (inputs.get("prompt") or "").strip()
        if not text:
            raise ValueError("prompt is required")
        if not inputs.get("output_path"):
            raise ValueError(
                "output_path is required - pass an explicit path inside the project workspace"
            )
        model = inputs.get("model_id", DEFAULT_MODEL)
        if model not in MODEL_IDS:
            raise ValueError(f"model_id must be one of {list(MODEL_IDS)}")
        output_format = inputs.get("output_format", DEFAULT_OUTPUT_FORMAT)
        if output_format not in OUTPUT_FORMATS:
            raise ValueError(f"output_format {output_format!r} is not supported by the API")

        body: dict[str, Any] = {"text": text, "model_id": model}
        duration = inputs.get("duration_seconds")
        if duration is not None:
            low, high = DURATION_RANGE_SECONDS
            if not low <= float(duration) <= high:
                raise ValueError(f"duration_seconds must be within [{low}, {high}]")
            body["duration_seconds"] = float(duration)
        loop = bool(inputs.get("loop", False))
        if loop and model not in LOOP_MODELS:
            raise ValueError(f"loop is only available on {sorted(LOOP_MODELS)}")
        body["loop"] = loop
        influence = inputs.get("prompt_influence", PROMPT_INFLUENCE_DEFAULT)
        if influence is not None:
            if not 0 <= float(influence) <= 1:
                raise ValueError("prompt_influence must be within [0, 1]")
            body["prompt_influence"] = float(influence)
        return body, {"output_format": output_format}

    # ---- execution --------------------------------------------------------

    @staticmethod
    def _target_path(inputs: dict[str, Any], output_format: str) -> Path:
        """The file this request will write (PCM is wrapped as .wav)."""
        path = Path(inputs["output_path"]).resolve()
        return path.with_suffix(".wav") if output_format.startswith("pcm_") else path

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="No ElevenLabs API key. " + self.install_instructions)
        try:
            body, params = self._build_request(inputs)
            estimated = self.estimate_cost(inputs)
        except ValueError as exc:
            return ToolResult(success=False, error=f"Invalid SFX request: {exc}",
                              data={"charge_status": "not_charged"})
        from lib.paid_call_guard import PaidCallNotAuthorized, check_paid_call

        try:
            check_paid_call(self.name, inputs)
        except PaidCallNotAuthorized as exc:
            return ToolResult(success=False, error=str(exc), data={"charge_status": "not_charged"})
        target = self._target_path(inputs, params["output_format"])
        if target.exists() and not inputs.get("overwrite"):
            # Checked before the paid request: refusing afterwards would waste it.
            return ToolResult(
                success=False,
                error=(f"Refusing a paid generation that would overwrite {target}. "
                       "Choose a new output_path; nothing was requested."),
                data={"charge_status": "not_charged", "existing_files": [str(target)]},
            )

        import requests

        start = time.time()
        try:
            response = requests.post(
                self._URL,
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                params=params,
                json=body,
                timeout=120,
            )
        except requests.RequestException as exc:
            return self._failure(
                f"request did not complete ({type(exc).__name__}); the generation may "
                "or may not have been billed",
                "unknown", estimated, start, api_key,
            )

        if response.status_code != 200:
            return self._failure(self._http_error(response), "not_charged", 0.0, start, api_key)
        if not response.content:
            return self._failure("empty audio body", "unknown", estimated, start, api_key)

        output_format = params["output_format"]
        path = target
        path.parent.mkdir(parents=True, exist_ok=True)
        if output_format.startswith("pcm_"):
            self._write_wav(path, response.content, int(output_format.split("_")[1]))
        else:
            path.write_bytes(response.content)

        measured = self._probe_duration(path)
        if body.get("duration_seconds") is not None:
            billed_seconds, basis = float(body["duration_seconds"]), "requested_duration"
        elif measured is not None:
            billed_seconds, basis = measured, "measured_output_duration"
        else:
            billed_seconds, basis = DURATION_RANGE_SECONDS[1], "maximum_duration_assumed"
        cost = round(billed_seconds / 60.0 * usd_per_minute(), 4)

        return ToolResult(
            success=True,
            data={
                "provider": "elevenlabs",
                "model": body["model_id"],
                "prompt": body["text"],
                "requested_duration_seconds": body.get("duration_seconds"),
                "duration_seconds": measured,
                "loop": body["loop"],
                "prompt_influence": body.get("prompt_influence"),
                "output_format": output_format,
                "output": str(path),
                "request_id": response.headers.get("request-id"),
                "charge_status": "charged",
                "billed_seconds": billed_seconds,
                "cost_basis": basis,
                "estimated_cost_usd": estimated,
                "usd_per_minute": usd_per_minute(),
            },
            artifacts=[str(path)],
            cost_usd=cost,
            duration_seconds=round(time.time() - start, 2),
            model=f"elevenlabs/{body['model_id']}",
        )

    @staticmethod
    def _http_error(response: Any) -> str:
        code = response.status_code
        detail = ""
        try:
            payload = response.json()
            raw = payload.get("detail") if isinstance(payload, dict) else payload
            if isinstance(raw, dict):
                detail = raw.get("message") or raw.get("status") or ""
            elif isinstance(raw, list):
                detail = "; ".join(str(item.get("msg", item)) for item in raw if item)
            elif raw:
                detail = str(raw)
        except Exception:
            detail = ""
        meaning = {
            401: "authentication failed - check ELEVENLABS_API_KEY",
            422: "invalid parameters",
            429: "rate limited or concurrency limit reached - retry later as a new paid call",
        }.get(code, "provider error")
        return f"HTTP {code} ({meaning}) {detail}".strip()

    def _failure(self, message: str, charge_status: str, cost: float,
                 start: float, api_key: str) -> ToolResult:
        text = f"ElevenLabs SFX failed: {message}"
        if api_key and api_key in text:
            text = text.replace(api_key, "[REDACTED]")
        return ToolResult(
            success=False,
            error=text,
            data={"provider": "elevenlabs", "charge_status": charge_status},
            # An unknown outcome is costed at the estimate, never at zero.
            cost_usd=cost,
            duration_seconds=round(time.time() - start, 2),
        )

    @staticmethod
    def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
        # The API returns headerless 16-bit little-endian mono PCM.
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)

    @staticmethod
    def _probe_duration(path: Path) -> Optional[float]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30, check=True,
            ).stdout.strip()
            return round(float(out), 3)
        except (subprocess.SubprocessError, ValueError, OSError):
            return None
