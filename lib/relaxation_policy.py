"""Duration and chunking policy for the relaxation pipeline.

The numbers live in `pipeline_defs/relaxation.yaml` under `metadata.duration`
and `metadata.chunking`. This module reads them from there rather than
restating them, so the manifest stays the single source of truth and a test
cannot pass against a policy the manifest no longer declares.

Why a module rather than prose: "validate 60 <= target_duration_seconds <=
18000" in a Director is an instruction an agent may skip. A function that
raises is a contract that can be tested.

    from lib.relaxation_policy import validate_duration, chunking_required

    validate_duration(900)          # ok
    validate_duration(59)           # DurationOutOfRange
    chunking_required(900)          # False - 15 minutes, chunking optional
    chunking_required(1800)         # True  - 30 minutes, chunk plan required
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

__all__ = [
    "DurationOutOfRange",
    "chunking_required",
    "duration_bounds",
    "chunk_threshold_seconds",
    "load_manifest",
    "validate_duration",
]

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "pipeline_defs" / "relaxation.yaml"


class DurationOutOfRange(ValueError):
    """Raised when a requested duration falls outside the supported range.

    Deliberately an error rather than a clamp. A 5-hour request that quietly
    becomes 3 hours, or a 30-second request that quietly becomes 60, is a
    production the operator did not ask for.
    """


@functools.lru_cache(maxsize=1)
def load_manifest(path: Optional[str] = None) -> Mapping[str, Any]:
    """Parse the relaxation manifest once."""
    return yaml.safe_load(Path(path or MANIFEST_PATH).read_text(encoding="utf-8"))


def _policy(manifest: Optional[Mapping[str, Any]], key: str) -> Mapping[str, Any]:
    data = (manifest or load_manifest()).get("metadata", {}).get(key)
    if not data:
        raise RuntimeError(
            f"relaxation.yaml no longer declares metadata.{key}; the pipeline's "
            f"{key} policy has no source of truth"
        )
    return data


def duration_bounds(manifest: Optional[Mapping[str, Any]] = None) -> tuple[int, int]:
    """(min_seconds, max_seconds) the pipeline supports."""
    policy = _policy(manifest, "duration")
    return int(policy["min_seconds"]), int(policy["max_seconds"])


def chunk_threshold_seconds(manifest: Optional[Mapping[str, Any]] = None) -> int:
    """Timeline length at which a chunk plan becomes required."""
    return int(_policy(manifest, "chunking")["threshold_seconds"])


def validate_duration(
    target_duration_seconds: float,
    manifest: Optional[Mapping[str, Any]] = None,
) -> float:
    """Return the duration, or raise `DurationOutOfRange`.

    Call this at the proposal stage. Surfacing the problem is the point; do not
    catch this and substitute a nearby value.
    """
    low, high = duration_bounds(manifest)
    if not low <= target_duration_seconds <= high:
        raise DurationOutOfRange(
            f"target_duration_seconds={target_duration_seconds} is outside the "
            f"supported range [{low}, {high}] "
            f"({low / 60:.0f} minute(s) to {high / 3600:.0f} hours). "
            "Surface this to the operator; do not clamp it."
        )
    return float(target_duration_seconds)


def chunking_required(
    timeline_seconds: float,
    manifest: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when `metadata.chunk_plan[]` is mandatory for this timeline.

    Below the threshold chunking is *optional* — permitted when render
    complexity calls for it, but never imposed. A 60-second piece does not get
    a chunk plan.
    """
    return timeline_seconds > chunk_threshold_seconds(manifest)
