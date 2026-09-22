"""Suno AI music generation via the sunoapi.org REST API.

Generates full songs, instrumentals, and background music. Async flow: submit
one generation request, poll for completion, download the audio.

One paid generation returns several candidate tracks (two, today). Every
candidate is downloaded and described, so a caller can evaluate all of them
without paying for another generation. `output_path` still receives the
candidate at `track_index`, for callers that expect a single file — but that is
a storage position, not an acceptance decision.

Contract verified against the live provider documentation on 2026-09-23:

- base URL ``https://api.sunoapi.org`` (docs quick start)
- ``POST /api/v1/generate``; ``GET /api/v1/generate/record-info?taskId=``;
  ``GET /api/v1/generate/credit`` (docs.sunoapi.org/suno-api/*)
- current models ``V6`` (provider default), ``V6_WILD``, ``V6_MINI``;
  ``V5_5``, ``V5``, ``V4_5PLUS``, ``V4_5ALL``, ``V4_5`` and ``V4`` are
  deprecated and kept only for backward compatibility
- ``duration`` (10-360 s) exists only in custom mode and only for V5_5/V6-family
- the HTTP status is 200 even for provider errors; the JSON ``code`` field
  carries the outcome (429 = insufficient credits, 430/405 = rate limited)
- tracks arrive under ``data.response.sunoData`` (older examples show
  ``data.response.data``; both are read)

Pricing: sunoapi.org publishes the value of a credit (``$0.005``) but not how
many credits a generation consumes. That figure is therefore **measured**: V6
was calibrated with live generations at both ends of its duration range and
costs the same 12 credits for each (see `VERIFIED_CREDITS_PER_GENERATION`), so
`estimate_cost` is fixed per generation across the whole range.
Only calibrated models are priced - nothing is inferred from a sibling model -
so ``V6_WILD``, ``V6_MINI`` and the deprecated models stay unpriced, and
``execute`` refuses to submit an unpriced call, until each is verified.

Every paid generation is reconciled from the account's credit balance, read
before and after the call. When the measured charge differs from the known
rate, the result carries ``pricing_mismatch`` and further paid generations of
that model are refused until the operator resolves it.

The calibrations also showed that V6 treats ``duration`` as a target: 10 s
requested returned 33.5 s and 17.8 s, while 360 s returned 359.9 s twice.
Count only the measured length of an accepted candidate, never the request.
"""

from __future__ import annotations

import math
import os
import time
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

#: Models the provider currently recommends. ``V6`` is its documented default.
CURRENT_MODELS = ("V6", "V6_WILD", "V6_MINI")
#: Still accepted by the provider "only for backward compatibility".
DEPRECATED_MODELS = ("V5_5", "V5", "V4_5PLUS", "V4_5ALL", "V4_5", "V4")
DEFAULT_MODEL = "V6"
#: Models that honour the custom-mode ``duration`` parameter.
DURATION_MODELS = frozenset({"V5_5", "V6", "V6_MINI", "V6_WILD"})
DURATION_RANGE_SECONDS = (10, 360)

# ---- Pricing (kept together so it is easy to update) ----------------------
#: Published on sunoapi.org: "Each credit is valued at $0.005 USD."
USD_PER_CREDIT = 0.005
#: Credits one generation request consumes, measured live. Only models listed
#: here are priced; a sibling model is never assumed to cost the same.
#:
#: V6 - two live generations, one at each end of the duration range:
#:   2026-09-22T20:28Z  10 s requested   1000 -> 988 credits (task db79ee16...)
#:                      candidates 33.5 s and 17.8 s
#:   2026-09-23         360 s requested   988 -> 976 credits (task d9e64977...)
#:                      candidates 359.9 s and 359.9 s
VERIFIED_CREDITS_PER_GENERATION: dict[str, float] = {"V6": 12.0}
#: Requested durations at which each verified price was measured. A price
#: verified at both ends of the range is fixed per generation across it.
PRICE_VERIFIED_AT_SECONDS: dict[str, tuple[float, ...]] = {"V6": (10.0, 360.0)}
#: Optional emergency override per model (``SUNO_CREDITS_PER_GENERATION_V6=14``)
#: for when the provider changes a price. Never required for a verified model.
OVERRIDE_ENV_PREFIX = "SUNO_CREDITS_PER_GENERATION_"

#: Models whose measured charge disagreed with the known rate in this process.
#: Paid generation of these models is refused until the operator resolves it.
#: (Across sessions, CostTracker persists the same block in cost_log.json.)
_PRICING_MISMATCHES: dict[str, dict[str, Any]] = {}


class SunoPricingUnconfirmed(ValueError):
    """No verified per-generation credit cost exists for this model."""


class SunoPricingMismatch(SunoPricingUnconfirmed):
    """The provider charged a different amount than the known rate."""


def resolve_price(model: str) -> tuple[float, str]:
    """(credits per generation, source) for ``model``, or raise.

    An operator override wins, then the calibrated constant. Nothing else is
    priced: an unverified model raises `SunoPricingUnconfirmed`, and a model
    with an unresolved charge mismatch raises `SunoPricingMismatch`.
    """
    if model in _PRICING_MISMATCHES:
        raise SunoPricingMismatch(
            f"Suno {model} pricing mismatch: {_PRICING_MISMATCHES[model]['message']} "
            "Further paid generations are refused until the rate is corrected "
            f"({OVERRIDE_ENV_PREFIX}{model}) and the mismatch is resolved."
        )
    env_name = f"{OVERRIDE_ENV_PREFIX}{model}"
    raw = os.environ.get(env_name, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError as exc:
            raise SunoPricingUnconfirmed(
                f"{env_name} must be a finite number greater than 0"
            ) from exc
        if not math.isfinite(value) or value <= 0:
            raise SunoPricingUnconfirmed(f"{env_name} must be a finite number greater than 0")
        return value, f"override:{env_name}"
    if model in VERIFIED_CREDITS_PER_GENERATION:
        return VERIFIED_CREDITS_PER_GENERATION[model], "verified_calibration"
    raise SunoPricingUnconfirmed(
        f"Suno pricing for model {model!r} has not been verified. sunoapi.org does "
        f"not publish credits per generation, and {model} is not inferred from "
        "another model. Verify it with one calibrated generation, or set "
        f"{env_name} from the sunoapi.org dashboard, before any paid generation."
    )


def credits_per_generation(model: str) -> float:
    """Verified credits per generation for ``model``, or raise."""
    return resolve_price(model)[0]


def resolve_pricing_mismatch(model: str) -> None:
    """Operator acknowledgement: allow paid generation of ``model`` again."""
    _PRICING_MISMATCHES.pop(model, None)


def check_charge(model: str, expected: float, consumed: Optional[float]) -> dict[str, Any]:
    """Compare a measured charge with the known rate; record a mismatch."""
    if consumed is None:
        return {"status": "unverified", "model": model, "expected_credits": expected,
                "measured_credits": None,
                "message": "the credit balance could not be measured around the call"}
    if abs(consumed - expected) < 1e-9:
        return {"status": "match", "model": model, "expected_credits": expected,
                "measured_credits": consumed}
    info = {
        "status": "mismatch", "model": model, "expected_credits": expected,
        "measured_credits": consumed,
        "message": f"expected {expected:g} credits, the provider charged {consumed:g}.",
    }
    _PRICING_MISMATCHES[model] = info
    return info


class SunoProviderError(RuntimeError):
    """A provider-reported failure. ``charge_status`` says whether credits moved."""

    def __init__(self, message: str, *, charge_status: str, code: Any = None) -> None:
        super().__init__(message)
        self.charge_status = charge_status
        self.code = code


class SunoMusic(BaseTool):
    name = "suno_music"
    version = "0.2.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "suno"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.ASYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = ["env:SUNO_API_KEY"]
    install_instructions = (
        "Set SUNO_API_KEY in .env (key from https://sunoapi.org/api-key). V6 "
        "pricing is built in (verified by calibration); other models stay "
        "unpriced until each is verified."
    )

    agent_skills = ["music"]

    capabilities = [
        "generate_background_music",
        "generate_song",
        "generate_instrumental",
    ]
    supports = {
        "instrumental": True,
        "vocals": True,
        "custom_lyrics": True,
        "style_control": True,
        "negative_tags": True,
        "duration_control": True,
        "multiple_candidates": True,
        "remaining_credits": True,
        "long_form": True,
    }
    best_for = [
        "high-quality instrumental background music",
        "full song generation with vocals and lyrics",
        "genre-specific music (any genre)",
        "multiple candidate tracks per paid generation",
    ]
    not_good_for = [
        "sound effects (use elevenlabs_sfx instead)",
        "sub-10-second stingers (minimum 10 s generation)",
        "offline generation",
    ]

    fallback_tools = ["music_gen"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["generate", "fetch", "credits"],
                "default": "generate",
                "description": (
                    "generate = one PAID generation request. fetch = re-poll and "
                    "download an existing task_id at no charge (e.g. after a "
                    "timeout). credits = read the remaining credit balance (free)."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Non-custom mode: a description of the desired music (max "
                    "3000 chars). Custom mode with vocals: the exact lyrics. "
                    "Custom mode instrumental: used as the style when `style` is "
                    "not given, otherwise not sent."
                ),
            },
            "style": {
                "type": "string",
                "description": "Style specification (max 1000 chars on V6-family, 200 on V4).",
            },
            "title": {
                "type": "string",
                "description": "Track title. Custom mode only (max 80 chars).",
            },
            "negative_tags": {
                "type": "string",
                "description": "Styles or traits to exclude. Custom mode only (max 1000 chars).",
            },
            "instrumental": {
                "type": "boolean",
                "default": True,
                "description": (
                    "True = no vocals. In custom mode the tool also adds vocal "
                    "terms to negative_tags so the no-vocal intent is explicit."
                ),
            },
            "custom_mode": {
                "type": "boolean",
                "default": False,
                "description": (
                    "False = simple mode (prompt is a description). True = custom "
                    "mode (style/title/negative_tags/duration available)."
                ),
            },
            "model": {
                "type": "string",
                "enum": list(CURRENT_MODELS + DEPRECATED_MODELS),
                "default": DEFAULT_MODEL,
                "description": (
                    "Suno model. V6 (default), V6_WILD and V6_MINI are current; "
                    "the V4/V5 values are deprecated by the provider."
                ),
            },
            "duration_seconds": {
                "type": "number",
                "minimum": DURATION_RANGE_SECONDS[0],
                "maximum": DURATION_RANGE_SECONDS[1],
                "description": (
                    "Requested track length. Custom mode with V5_5/V6-family "
                    "only. The provider's own default is 20 s, so set it."
                ),
            },
            "style_weight": {"type": "number", "minimum": 0, "maximum": 1},
            "weirdness_constraint": {"type": "number", "minimum": 0, "maximum": 1},
            "variety": {"type": "integer", "minimum": 0, "maximum": 4},
            "output_path": {
                "type": "string",
                "description": (
                    "Required for generate/fetch. Receives the candidate at "
                    "track_index; other candidates are written beside it."
                ),
            },
            "track_index": {
                "type": "integer",
                "default": 0,
                "minimum": 0,
                "description": "Which candidate is written to output_path.",
            },
            "task_id": {"type": "string", "description": "Existing task (operation=fetch)."},
            "callback_url": {
                "type": "string",
                "description": (
                    "The provider requires a callBackUrl; this tool polls "
                    "instead. Defaults to SUNO_CALLBACK_URL, else a reserved "
                    "non-resolving .invalid address so no data goes elsewhere."
                ),
            },
            "max_wait_seconds": {"type": "number", "minimum": 30},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=200, network_required=True
    )
    # The paid POST is never retried inside the tool: a retry is a new paid
    # generation, and must go through budget reservation like any other.
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = [
        "prompt", "style", "negative_tags", "instrumental", "custom_mode",
        "model", "duration_seconds",
    ]
    side_effects = [
        "writes every generated candidate under output_path's directory",
        "calls Suno API via sunoapi.org (one paid generation per generate call)",
    ]
    user_visible_verification = [
        "Listen to every candidate for mood, genre accuracy, vocals and quality",
    ]

    _BASE_URL = "https://api.sunoapi.org/api/v1"
    _DEFAULT_CALLBACK = "https://callback.invalid/openmontage/suno"
    _POLL_INTERVAL = 30  # seconds; the docs poll at 30 s
    _MAX_WAIT = 600  # V6 tracks run to 6 minutes
    _MAX_POLL_ERRORS = 3  # consecutive failed status reads tolerated
    _DOWNLOAD_ATTEMPTS = 3  # downloads are free; bounded anyway
    _BACKOFF = 5.0

    # ---- status / pricing -------------------------------------------------

    def _get_api_key(self) -> Optional[str]:
        return os.environ.get("SUNO_API_KEY") or None

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["provider_backend"] = "sunoapi.org"
        info["models"] = {
            "current": list(CURRENT_MODELS),
            "deprecated": list(DEPRECATED_MODELS),
            "default": DEFAULT_MODEL,
        }
        info["pricing"] = {m: self.pricing_status(m) for m in CURRENT_MODELS}
        return info

    def pricing_status(self, model: str = DEFAULT_MODEL) -> dict[str, Any]:
        """Whether a paid generation of ``model`` can be priced right now."""
        try:
            credits, source = resolve_price(model)
        except SunoPricingUnconfirmed as exc:
            return {
                "confirmed": False,
                "model": model,
                "usd_per_credit": USD_PER_CREDIT,
                "credits_per_generation": None,
                "mismatch": _PRICING_MISMATCHES.get(model),
                "reason": str(exc),
            }
        return {
            "confirmed": True,
            "model": model,
            "source": source,
            "verified_at_requested_seconds": list(PRICE_VERIFIED_AT_SECONDS.get(model, ())),
            "usd_per_credit": USD_PER_CREDIT,
            "credits_per_generation": credits,
            "usd_per_generation": round(credits * USD_PER_CREDIT, 4),
        }

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """USD for one call. Raises `SunoPricingUnconfirmed` when unpriced."""
        if inputs.get("operation", "generate") != "generate":
            return 0.0
        model = inputs.get("model", DEFAULT_MODEL)
        return round(credits_per_generation(model) * USD_PER_CREDIT, 4)

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Validate and price locally; never contacts the provider."""
        result: dict[str, Any] = {
            "tool": self.name,
            "status": self.get_status().value,
            "estimated_runtime_seconds": self.estimate_runtime(inputs),
        }
        try:
            payload = self._build_payload(inputs)
            result["payload_preview"] = payload
            result["estimated_cost_usd"] = self.estimate_cost(inputs)
            result["would_execute"] = result["status"] == ToolStatus.AVAILABLE.value
        except (SunoPricingUnconfirmed, ValueError) as exc:
            result["estimated_cost_usd"] = None
            result["would_execute"] = False
            result["blocker"] = str(exc)
        return result

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 120.0 if inputs.get("operation", "generate") == "generate" else 5.0

    # ---- execution --------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="No Suno API key. " + self.install_instructions)

        operation = inputs.get("operation", "generate")
        try:
            if operation == "credits":
                credits = self._remaining_credits(api_key)
                return ToolResult(
                    success=True,
                    data={
                        "provider": "suno",
                        "provider_backend": "sunoapi.org",
                        "operation": "credits",
                        "credits": credits,
                        "usd_value": round(credits * USD_PER_CREDIT, 4),
                        "usd_per_credit": USD_PER_CREDIT,
                    },
                    cost_usd=0.0,
                )
            if operation == "fetch":
                return self._fetch_existing(inputs, api_key)
            if operation != "generate":
                return ToolResult(success=False, error=f"Unknown operation {operation!r}")
            return self._generate(inputs, api_key)
        except Exception as exc:  # never leak the key through an exception text
            return ToolResult(success=False, error=self._redact(f"Suno call failed: {exc}", api_key))

    def _generate(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        model = inputs.get("model", DEFAULT_MODEL)
        try:
            payload = self._build_payload(inputs)
            self._require_output_path(inputs)
            estimated = self.estimate_cost(inputs)
            expected_credits = credits_per_generation(model)
        except SunoPricingUnconfirmed as exc:
            return ToolResult(success=False, error=str(exc), data={"charge_status": "not_charged"})
        except ValueError as exc:
            return ToolResult(success=False, error=f"Invalid Suno request: {exc}",
                              data={"charge_status": "not_charged"})

        start = time.time()
        credits_before = self._try_credits(api_key)

        try:
            task_id = self._submit(payload, api_key)
        except SunoProviderError as exc:
            return self._failure(exc, api_key, model=model, estimated=estimated,
                                 expected_credits=expected_credits,
                                 credits_before=credits_before, task_id=None, start=start)

        try:
            record = self._poll(task_id, api_key, inputs.get("max_wait_seconds"))
            tracks = self._extract_tracks(record)
            if not tracks:
                raise SunoProviderError("Suno reported SUCCESS but returned no tracks",
                                        charge_status="charged")
            candidates = self._download_candidates(tracks, inputs)
        except SunoProviderError as exc:
            return self._failure(exc, api_key, model=model, estimated=estimated,
                                 expected_credits=expected_credits,
                                 credits_before=credits_before, task_id=task_id, start=start)
        except TimeoutError as exc:
            err = SunoProviderError(
                f"{exc}. The task is still billed; recover it with operation=fetch "
                f"and task_id={task_id} instead of generating again.",
                charge_status="charged",
            )
            return self._failure(err, api_key, model=model, estimated=estimated,
                                 expected_credits=expected_credits,
                                 credits_before=credits_before, task_id=task_id, start=start)

        cost, basis, credits_after, consumed = self._actual_cost(
            api_key, credits_before, estimated)
        result = self._success(
            inputs, model=model, task_id=task_id, candidates=candidates,
            cost=cost, cost_basis=basis, estimated=estimated,
            credits_before=credits_before, credits_after=credits_after,
            credits_consumed=consumed, start=start,
        )
        self._attach_pricing_check(result, model, expected_credits, consumed)
        return result

    def _fetch_existing(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        task_id = inputs.get("task_id")
        if not task_id:
            return ToolResult(success=False, error="operation=fetch requires task_id")
        try:
            self._require_output_path(inputs)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        start = time.time()
        try:
            record = self._poll(task_id, api_key, inputs.get("max_wait_seconds"), first_wait=0)
            tracks = self._extract_tracks(record)
            if not tracks:
                return ToolResult(success=False, error=f"Task {task_id} has no tracks")
            candidates = self._download_candidates(tracks, inputs)
        except (SunoProviderError, TimeoutError) as exc:
            return ToolResult(success=False, error=self._redact(str(exc), api_key),
                              data={"task_id": task_id, "charge_status": "not_charged"})
        return self._success(
            inputs, model=inputs.get("model", DEFAULT_MODEL), task_id=task_id,
            candidates=candidates, cost=0.0, cost_basis="fetch_existing_task",
            estimated=0.0, credits_before=None, credits_after=None,
            credits_consumed=None, start=start,
        )

    # ---- request construction --------------------------------------------

    @staticmethod
    def _require_output_path(inputs: dict[str, Any]) -> Path:
        raw = inputs.get("output_path")
        if not raw:
            raise ValueError(
                "output_path is required - pass an explicit path inside the "
                "project workspace (the tool never writes to the working directory)"
            )
        return Path(raw)

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Translate tool inputs into the provider's documented request body."""
        model = inputs.get("model", DEFAULT_MODEL)
        if model not in CURRENT_MODELS + DEPRECATED_MODELS:
            raise ValueError(
                f"model {model!r} is not a sunoapi.org model; use one of "
                f"{', '.join(CURRENT_MODELS)} (current) or {', '.join(DEPRECATED_MODELS)} (deprecated)"
            )
        custom = bool(inputs.get("custom_mode", False))
        instrumental = bool(inputs.get("instrumental", True))
        prompt = (inputs.get("prompt") or "").strip()
        style = (inputs.get("style") or "").strip()
        style_limit = 200 if model == "V4" else 1000

        payload: dict[str, Any] = {
            "model": model,
            "customMode": custom,
            "instrumental": instrumental,
            "callBackUrl": (
                inputs.get("callback_url")
                or os.environ.get("SUNO_CALLBACK_URL")
                or self._DEFAULT_CALLBACK
            ),
        }

        custom_only = ("title", "negative_tags", "duration_seconds",
                       "style_weight", "weirdness_constraint", "variety")
        if not custom:
            passed = [k for k in custom_only if inputs.get(k) is not None]
            if passed:
                raise ValueError(
                    f"{', '.join(passed)} require custom_mode=true (the provider "
                    "rejects them in non-custom mode)"
                )
            if not (prompt or style):
                raise ValueError("non-custom mode needs a prompt or a style")
            if prompt:
                payload["prompt"] = prompt[:3000]
            if style:
                payload["style"] = style[:style_limit]
            return payload

        if instrumental:
            # The prompt would be sung as lyrics; for an instrumental it is the
            # style description when no explicit style was given.
            style = style or prompt
        elif prompt:
            payload["prompt"] = prompt[: 3000 if model == "V4" else 5000]

        negative = (inputs.get("negative_tags") or "").strip()
        if instrumental:
            negative = self._merge_tags(negative, ("vocals", "singing", "spoken word"))
        if not (style or negative or payload.get("prompt")):
            raise ValueError("custom mode needs a style, lyrics or negative_tags")
        if style:
            payload["style"] = style[:style_limit]
        if negative:
            payload["negativeTags"] = negative[:1000]
        if inputs.get("title"):
            payload["title"] = str(inputs["title"])[:80]

        duration = inputs.get("duration_seconds")
        if duration is not None:
            if model not in DURATION_MODELS:
                raise ValueError(
                    f"duration_seconds is only honoured by {sorted(DURATION_MODELS)}; "
                    f"model {model!r} ignores it"
                )
            low, high = DURATION_RANGE_SECONDS
            if not low <= float(duration) <= high:
                raise ValueError(f"duration_seconds must be within [{low}, {high}]")
            payload["duration"] = float(duration)

        for src, dst, low, high in (
            ("style_weight", "styleWeight", 0, 1),
            ("weirdness_constraint", "weirdnessConstraint", 0, 1),
            ("variety", "variety", 0, 4),
        ):
            value = inputs.get(src)
            if value is None:
                continue
            if not low <= value <= high:
                raise ValueError(f"{src} must be within [{low}, {high}]")
            payload[dst] = int(value) if dst == "variety" else round(float(value), 2)
        return payload

    @staticmethod
    def _merge_tags(existing: str, required: tuple[str, ...]) -> str:
        tags = [t.strip() for t in existing.split(",") if t.strip()]
        lowered = {t.lower() for t in tags}
        tags.extend(t for t in required if t not in lowered)
        return ", ".join(tags)

    # ---- provider calls ----------------------------------------------------

    def _headers(self, api_key: str, *, json_body: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _body_error(body: Any, *, charged: str) -> Optional[SunoProviderError]:
        """Map the provider's in-body ``code`` to an error, or None when OK."""
        if not isinstance(body, dict):
            return SunoProviderError("unreadable provider response", charge_status=charged)
        code = body.get("code")
        if code in (None, 200):
            return None
        meaning = {
            400: "invalid parameters",
            401: "unauthorized - check SUNO_API_KEY",
            404: "invalid request method or path",
            405: "rate limit exceeded",
            413: "prompt or style too long",
            429: "insufficient credits",
            430: "call frequency too high",
            455: "provider maintenance",
            500: "provider server error",
        }.get(code, "provider error")
        msg = body.get("msg") or ""
        return SunoProviderError(f"sunoapi.org code {code} ({meaning}): {msg}".rstrip(": "),
                                 charge_status=charged, code=code)

    def _submit(self, payload: dict[str, Any], api_key: str) -> str:
        """Send the ONE paid request. Never retried here."""
        import requests

        try:
            response = requests.post(
                f"{self._BASE_URL}/generate",
                headers=self._headers(api_key, json_body=True),
                json=payload,
                timeout=60,
            )
        except requests.RequestException as exc:
            # The request may or may not have reached the provider.
            raise SunoProviderError(
                f"submit did not complete ({type(exc).__name__}); the provider may "
                "or may not have started a billed task - check the sunoapi.org logs "
                "before retrying",
                charge_status="unknown",
            ) from None
        if response.status_code == 401:
            raise SunoProviderError("unauthorized - check SUNO_API_KEY",
                                    charge_status="not_charged", code=401)
        if response.status_code >= 400:
            raise SunoProviderError(f"HTTP {response.status_code} from sunoapi.org",
                                    charge_status="not_charged", code=response.status_code)
        try:
            body = response.json()
        except ValueError:
            body = None
        error = self._body_error(body, charged="not_charged")
        if error:
            raise error
        task_id = (body.get("data") or {}).get("taskId") if isinstance(body.get("data"), dict) else None
        if not task_id:
            raise SunoProviderError("no taskId in the submit response",
                                    charge_status="unknown")
        return str(task_id)

    def _poll(self, task_id: str, api_key: str, max_wait: Any = None,
              first_wait: Optional[float] = None) -> dict[str, Any]:
        """Poll record-info until SUCCESS. Status reads are free and bounded."""
        import requests

        limit = float(max_wait or self._MAX_WAIT)
        # Bounded by attempts as well as time, so a zero interval cannot spin.
        max_attempts = int(limit // max(self._POLL_INTERVAL, 1)) + 1
        errors = 0
        delay = self._POLL_INTERVAL if first_wait is None else first_wait
        for _attempt in range(max_attempts):
            if delay:
                time.sleep(delay)
            delay = self._POLL_INTERVAL
            try:
                response = requests.get(
                    f"{self._BASE_URL}/generate/record-info",
                    params={"taskId": task_id},
                    headers=self._headers(api_key),
                    timeout=30,
                )
                response.raise_for_status()
                body = response.json()
            except (requests.RequestException, ValueError) as exc:
                errors += 1
                if errors >= self._MAX_POLL_ERRORS:
                    raise TimeoutError(
                        f"status reads for task {task_id} failed {errors} times "
                        f"({type(exc).__name__})"
                    ) from None
            else:
                error = self._body_error(body, charged="charged")
                if error and error.code in (405, 430):
                    errors += 1
                    if errors >= self._MAX_POLL_ERRORS:
                        raise TimeoutError(f"status reads for task {task_id} kept being rate limited")
                elif error:
                    raise error
                else:
                    errors = 0
                    data = body.get("data") or {}
                    status = str(data.get("status") or "")
                    if status == "SUCCESS":
                        return data
                    if status in ("CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED",
                                  "CALLBACK_EXCEPTION", "SENSITIVE_WORD_ERROR"):
                        detail = data.get("errorMessage") or ""
                        raise SunoProviderError(
                            f"Suno task {task_id} ended with {status} {detail}".strip(),
                            charge_status="unknown", code=data.get("errorCode"),
                        )
                    # PENDING, TEXT_SUCCESS, FIRST_SUCCESS: keep polling.
        raise TimeoutError(f"Suno task {task_id} not finished after {max_attempts} status reads")

    @staticmethod
    def _extract_tracks(record: dict[str, Any]) -> list[dict[str, Any]]:
        response = record.get("response") or {}
        for candidate in (response.get("sunoData"), response.get("data"), record.get("data")):
            if isinstance(candidate, list) and candidate:
                return [t for t in candidate if isinstance(t, dict)]
        return []

    def _download_candidates(self, tracks: list[dict[str, Any]],
                             inputs: dict[str, Any]) -> list[dict[str, Any]]:
        """Download every candidate the paid generation produced."""
        primary = self._require_output_path(inputs).resolve()
        primary.parent.mkdir(parents=True, exist_ok=True)
        index = int(inputs.get("track_index", 0))
        if not 0 <= index < len(tracks):
            index = 0
        suffix = primary.suffix or ".mp3"

        candidates: list[dict[str, Any]] = []
        for i, track in enumerate(tracks):
            path = primary if i == index else primary.with_name(f"{primary.stem}__cand{i}{suffix}")
            url = track.get("audio_url") or track.get("source_audio_url")
            entry = {
                "index": i,
                "track_id": track.get("id"),
                "title": track.get("title"),
                "tags": track.get("tags"),
                "model_name": track.get("model_name"),
                "duration_seconds": track.get("duration"),
                "path": None,
                "downloaded": False,
                "written_to_output_path": i == index,
            }
            if url:
                self._download(url, path)
                entry["path"] = str(path)
                entry["downloaded"] = True
            else:
                entry["error"] = "no audio_url for this candidate"
            candidates.append(entry)
        if not any(c["downloaded"] for c in candidates):
            raise SunoProviderError("no candidate could be downloaded", charge_status="charged")
        return candidates

    def _download(self, url: str, path: Path) -> None:
        import requests

        last: Optional[Exception] = None
        for attempt in range(self._DOWNLOAD_ATTEMPTS):
            try:
                response = requests.get(url, timeout=180)
                response.raise_for_status()
                if not response.content:
                    raise ValueError("empty audio body")
                path.write_bytes(response.content)
                return
            except (requests.RequestException, ValueError) as exc:
                last = exc
                if attempt + 1 < self._DOWNLOAD_ATTEMPTS:
                    time.sleep(self._BACKOFF)
        raise SunoProviderError(
            f"download failed after {self._DOWNLOAD_ATTEMPTS} attempts "
            f"({type(last).__name__}); the generation is paid - recover with operation=fetch",
            charge_status="charged",
        )

    def _remaining_credits(self, api_key: str) -> float:
        import requests

        response = requests.get(
            f"{self._BASE_URL}/generate/credit",
            headers=self._headers(api_key),
            timeout=30,
        )
        if response.status_code == 401:
            raise SunoProviderError("unauthorized - check SUNO_API_KEY",
                                    charge_status="not_charged", code=401)
        response.raise_for_status()
        body = response.json()
        error = self._body_error(body, charged="not_charged")
        if error:
            raise error
        value = body.get("data")
        if not isinstance(value, (int, float)):
            raise SunoProviderError("credit balance missing from response",
                                    charge_status="not_charged")
        return float(value)

    def _try_credits(self, api_key: str) -> Optional[float]:
        try:
            return self._remaining_credits(api_key)
        except Exception:
            return None

    # ---- cost reconciliation ----------------------------------------------

    def _actual_cost(self, api_key: str, before: Optional[float],
                     estimated: float) -> tuple[float, str, Optional[float], Optional[float]]:
        """Measured spend from the balance delta, else the confirmed estimate.

        The delta is attributable only when nothing else spends on the same key
        concurrently; a negative or implausible delta falls back to the estimate.
        """
        after = self._try_credits(api_key) if before is not None else None
        if before is not None and after is not None:
            consumed = round(before - after, 4)
            if consumed >= 0:
                return round(consumed * USD_PER_CREDIT, 4), "measured_credit_delta", after, consumed
        return estimated, "confirmed_estimate", after, None

    def _failure(self, exc: SunoProviderError, api_key: str, *, model: str,
                 estimated: float, expected_credits: float,
                 credits_before: Optional[float],
                 task_id: Optional[str], start: float) -> ToolResult:
        charge_status = exc.charge_status
        if charge_status == "not_charged":
            cost, basis, after, consumed = 0.0, "not_charged", None, None
        else:
            cost, basis, after, consumed = self._actual_cost(api_key, credits_before, estimated)
            if basis == "measured_credit_delta":
                charge_status = "charged" if consumed and consumed > 0 else "not_charged"
            # Otherwise an unknown outcome is costed at the estimate, never at 0.
        result = ToolResult(
            success=False,
            error=self._redact(f"Suno generation failed: {exc}", api_key),
            data={
                "provider": "suno",
                "provider_backend": "sunoapi.org",
                "model": model,
                "task_id": task_id,
                "provider_code": exc.code,
                "charge_status": charge_status,
                "cost_basis": basis,
                "estimated_cost_usd": estimated,
                "credits_before": credits_before,
                "credits_after": after,
                "credits_consumed": consumed,
            },
            cost_usd=cost,
            duration_seconds=round(time.time() - start, 2),
            model=f"suno/{model}",
        )
        if consumed:  # a charge was measured - hold it to the known rate too
            self._attach_pricing_check(result, model, expected_credits, consumed)
        return result

    @staticmethod
    def _attach_pricing_check(result: ToolResult, model: str, expected: float,
                              consumed: Optional[float]) -> None:
        check = check_charge(model, expected, consumed)
        result.data["pricing_check"] = check
        if check["status"] == "mismatch":
            result.data["pricing_mismatch"] = check
            note = (f"PRICING MISMATCH for Suno {model}: {check['message']} Further "
                    f"paid {model} generations are blocked until this is resolved.")
            result.error = f"{result.error} | {note}" if result.error else note

    def _success(self, inputs: dict[str, Any], *, model: str, task_id: str,
                 candidates: list[dict[str, Any]], cost: float, cost_basis: str,
                 estimated: float, credits_before: Optional[float],
                 credits_after: Optional[float], credits_consumed: Optional[float],
                 start: float) -> ToolResult:
        primary = next(c for c in candidates if c["written_to_output_path"])
        if not primary["downloaded"]:
            primary = next(c for c in candidates if c["downloaded"])
        return ToolResult(
            success=True,
            data={
                "provider": "suno",
                "provider_backend": "sunoapi.org",
                "model": model,
                "prompt": inputs.get("prompt"),
                "style": inputs.get("style"),
                "negative_tags": inputs.get("negative_tags"),
                "title": primary.get("title") or inputs.get("title"),
                "instrumental": bool(inputs.get("instrumental", True)),
                "custom_mode": bool(inputs.get("custom_mode", False)),
                "requested_duration_seconds": inputs.get("duration_seconds"),
                "duration_seconds": primary.get("duration_seconds"),
                "output": primary["path"],
                "format": "mp3",
                "track_id": primary.get("track_id"),
                "task_id": task_id,
                "tracks_generated": len(candidates),
                "candidates": candidates,
                # Where a file sits is not a creative decision.
                "selection_status": "unreviewed",
                "charge_status": (
                    "no_new_charge" if cost_basis == "fetch_existing_task" else "charged"
                ),
                "cost_basis": cost_basis,
                "estimated_cost_usd": estimated,
                "credits_before": credits_before,
                "credits_after": credits_after,
                "credits_consumed": credits_consumed,
                "usd_per_credit": USD_PER_CREDIT,
            },
            artifacts=[c["path"] for c in candidates if c["downloaded"]],
            cost_usd=cost,
            duration_seconds=round(time.time() - start, 2),
            model=f"suno/{model}",
        )

    @staticmethod
    def _redact(text: str, api_key: Optional[str]) -> str:
        if api_key and api_key in text:
            text = text.replace(api_key, "[REDACTED]")
        return text
