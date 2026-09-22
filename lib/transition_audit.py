"""Make the rendered picture match the approved transition map.

Three defects in the second test render came from the gap between what the
edit approved and what the renderer produced. All three are arithmetic or
bookkeeping, not taste, so all three are checkable:

1. **A technical boundary silently replaced an editorial one.** The approved
   edit put a 1.2 s dissolve at every shot join. The chunk plan put chunk
   boundaries at the four movement joins, and the renderer joined chunks with
   the concat demuxer — which cannot dissolve. The render report recorded the
   substitution as a fact ("movement boundaries are straight cuts") and
   nothing failed. The delivered film therefore contained three hard cuts the
   operator never approved, measured here at 20.2, 23.5 and 26.2 mean
   absolute frame difference against a local baseline of 1.4-3.7.

2. **Overlap arithmetic went unchecked.** Each dissolve consumes its duration
   from the timeline, so 56 slots totalling 918.3 s joined by 55 dissolves of
   1.2 s run for 852.3 s, not 918.3 s. Because three dissolves became cuts,
   the delivered file ran 3.6 s longer than predicted. Nobody noticed,
   because no check compared predicted duration to the transition map.

3. **A uniform map is not a decision.** 55 of 56 joins were the same 1.2 s
   dissolve. `transition_discipline` reports that shape so a reviewer sees it
   before a render rather than after.

This module holds no editorial opinion about *which* transition belongs at a
given join — that is the Edit Director's judgement. It checks that the
approved choice survives into the file.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from lib.ffmpeg_runtime import FFmpegNotAvailable, ffmpeg_path

logger = logging.getLogger(__name__)

#: Transition types that consume timeline by overlapping two shots.
OVERLAPPING_TYPES = frozenset({"crossfade", "dissolve", "xfade"})

#: Types that occupy time without overlapping the neighbouring shots.
NON_OVERLAPPING_TYPES = frozenset({"cut", "fade", "fade_to_black", "fade_through_black"})

#: A chunk boundary must clear an approved dissolve by at least this much, or
#: the dissolve cannot be rendered inside a single chunk.
BOUNDARY_CLEARANCE_SECONDS = 0.5

#: Observed frame-difference peak, as a multiple of the local baseline, above
#: which a boundary reads as a hard cut. Measured margin on real renders is
#: wide: dissolves measured up to 3.9x, unexpected hard cuts 10.7-26.3x.
CUT_SPIKE_RATIO = 6.0

#: An absolute floor so a very flat, dark scene cannot produce a false cut.
CUT_SPIKE_FLOOR = 8.0


class TransitionAuditError(RuntimeError):
    """Raised when an audit cannot be performed."""


# --------------------------------------------------------------------------
# Plan-level checks: run these BEFORE the long encode
# --------------------------------------------------------------------------


@dataclass
class Transition:
    """One approved boundary."""

    at_seconds: float
    type: str
    duration_seconds: float = 0.0

    @property
    def overlaps(self) -> bool:
        return self.type in OVERLAPPING_TYPES and self.duration_seconds > 0

    @property
    def span(self) -> tuple[float, float]:
        """The interval the transition occupies on the timeline."""
        half = self.duration_seconds / 2.0
        return (self.at_seconds - half, self.at_seconds + half)


def as_transitions(raw: Iterable[Mapping[str, Any]]) -> list[Transition]:
    """Normalise `edit_decisions.transitions[]` entries."""
    out: list[Transition] = []
    for item in raw:
        out.append(
            Transition(
                at_seconds=float(item.get("at_seconds", 0.0)),
                type=str(item.get("type", "cut")).lower(),
                duration_seconds=float(item.get("duration_seconds", 0.0) or 0.0),
            )
        )
    return sorted(out, key=lambda t: t.at_seconds)


def timeline_duration(
    slot_holds: Sequence[float], transitions: Sequence[Transition]
) -> float:
    """Duration after overlap, which is what the file will actually run.

    Every overlapping transition removes its own duration from the sum of the
    holds, because the two shots play simultaneously for that long. Reporting
    the raw sum of holds as the timeline duration is the error that made the
    Test 2 prediction disagree with the render.
    """
    overlap = sum(t.duration_seconds for t in transitions if t.overlaps)
    return round(sum(slot_holds) - overlap, 3)


@dataclass
class ChunkBoundaryViolation:
    """A chunk boundary that cannot carry its approved transition."""

    boundary_seconds: float
    transition_type: str
    transition_at_seconds: float
    transition_duration_seconds: float
    clearance_seconds: float

    def describe(self) -> str:
        return (
            f"chunk boundary at {self.boundary_seconds:.2f}s falls "
            f"{self.clearance_seconds:.2f}s from an approved "
            f"{self.transition_duration_seconds:.2f}s {self.transition_type} at "
            f"{self.transition_at_seconds:.2f}s - concat assembly cannot "
            "dissolve across a chunk join, so this transition would be "
            "silently replaced by a hard cut"
        )


def validate_chunk_plan(
    transitions: Sequence[Transition],
    chunk_boundaries: Sequence[float],
    *,
    clearance_seconds: float = BOUNDARY_CLEARANCE_SECONDS,
) -> list[ChunkBoundaryViolation]:
    """Check that no chunk boundary lands on an approved dissolve.

    Returns the violations. An empty list means the chunk plan can be rendered
    without changing the edit. A non-empty list is not a licence to change the
    transition: move the boundary to a straight cut, or render the boundary
    region as its own short stitched segment. The editorial intent is the
    fixed quantity.
    """
    violations: list[ChunkBoundaryViolation] = []
    for boundary in chunk_boundaries:
        for transition in transitions:
            if not transition.overlaps:
                continue
            start, end = transition.span
            if start - clearance_seconds <= boundary <= end + clearance_seconds:
                violations.append(
                    ChunkBoundaryViolation(
                        boundary_seconds=round(boundary, 3),
                        transition_type=transition.type,
                        transition_at_seconds=round(transition.at_seconds, 3),
                        transition_duration_seconds=transition.duration_seconds,
                        clearance_seconds=round(
                            min(abs(boundary - start), abs(boundary - end)), 3
                        ),
                    )
                )
    return violations


def safe_chunk_boundaries(
    transitions: Sequence[Transition],
    *,
    target_chunk_seconds: float,
    total_seconds: float,
    clearance_seconds: float = BOUNDARY_CLEARANCE_SECONDS,
) -> list[float]:
    """Propose chunk boundaries that fall only on straight cuts.

    Walks the approved transition map and picks the non-overlapping boundary
    nearest each target position. This adjusts the chunk plan to the edit,
    which is the correct direction: the render is the servant of the approved
    picture, not its editor.
    """
    cuts = [
        t.at_seconds
        for t in transitions
        if not t.overlaps and 0.0 < t.at_seconds < total_seconds
    ]
    if not cuts:
        return []

    boundaries: list[float] = []
    position = target_chunk_seconds
    while position < total_seconds - target_chunk_seconds / 2:
        candidates = [
            c
            for c in cuts
            if c not in boundaries
            and not validate_chunk_plan(transitions, [c], clearance_seconds=clearance_seconds)
        ]
        if not candidates:
            break
        nearest = min(candidates, key=lambda c: abs(c - position))
        boundaries.append(nearest)
        position = nearest + target_chunk_seconds
    return sorted(boundaries)


@dataclass
class TransitionDiscipline:
    """The shape of a transition map, for a reviewer to judge before a render."""

    total_boundaries: int
    counts_by_type: dict[str, int]
    dominant_type: Optional[str]
    dominant_share: float
    distinct_durations: list[float]
    dissolve_share: float
    longest_identical_run: int
    findings: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "total_boundaries": self.total_boundaries,
            "counts_by_type": self.counts_by_type,
            "dominant_type": self.dominant_type,
            "dominant_share": self.dominant_share,
            "dissolve_share": self.dissolve_share,
            "distinct_durations": self.distinct_durations,
            "longest_identical_run": self.longest_identical_run,
            "findings": self.findings,
        }


#: A share is only meaningful once there are enough joins to have a ratio.
#: Below this the map is reported but its dissolve share is not judged: a
#: four-shot preview with two dissolves is 67%, which says nothing about
#: editorial discipline.
MIN_JOINS_FOR_SHARE = 8


def transition_discipline(
    transitions: Sequence[Transition],
    *,
    max_dissolve_share: float = 0.5,
    max_identical_run: int = 6,
    min_joins_for_share: int = MIN_JOINS_FOR_SHARE,
) -> TransitionDiscipline:
    """Describe a transition map's uniformity.

    The thresholds are advisory defaults: a channel that wants a different
    editorial ratio sets its own. What is not advisory is that the shape gets
    reported, so "every join is the same dissolve" is visible as a decision
    rather than discovered in the delivered film.
    """
    joins = [t for t in transitions if t.at_seconds > 0]
    total = len(joins)
    counts: dict[str, int] = {}
    for transition in joins:
        counts[transition.type] = counts.get(transition.type, 0) + 1

    findings: list[str] = []
    dominant = max(counts, key=lambda k: counts[k]) if counts else None
    dominant_share = round(counts[dominant] / total, 3) if dominant and total else 0.0

    dissolves = sum(count for t, count in counts.items() if t in OVERLAPPING_TYPES)
    dissolve_share = round(dissolves / total, 3) if total else 0.0

    durations = sorted(
        {round(t.duration_seconds, 3) for t in joins if t.duration_seconds > 0}
    )

    longest_run = 0
    longest_run_key: Optional[tuple[str, float]] = None
    current_run = 0
    previous: Optional[tuple[str, float]] = None
    for transition in joins:
        key = (transition.type, round(transition.duration_seconds, 3))
        current_run = current_run + 1 if key == previous else 1
        previous = key
        if current_run > longest_run:
            longest_run, longest_run_key = current_run, key

    if total >= min_joins_for_share and dissolve_share > max_dissolve_share:
        findings.append(
            f"{dissolve_share:.0%} of joins are dissolves. A dissolve is a "
            "choice for compatible shots; making it the default turns the film "
            "into a continuous blur and removes the straight cut's calm."
        )
    if longest_run > max_identical_run:
        run_type, run_duration = longest_run_key or ("transition", 0.0)
        findings.append(
            f"{longest_run} consecutive joins use an identical {run_duration}s "
            f"{run_type} - a mechanical pattern rather than a set of decisions."
        )
    if len(durations) == 1 and dissolves > max_identical_run:
        findings.append(
            f"every dissolve is exactly {durations[0]}s. Dissolve length should "
            "follow the shots it joins, not a constant."
        )

    return TransitionDiscipline(
        total_boundaries=total,
        counts_by_type=counts,
        dominant_type=dominant,
        dominant_share=dominant_share,
        distinct_durations=durations,
        dissolve_share=dissolve_share,
        longest_identical_run=longest_run,
        findings=findings,
    )


# --------------------------------------------------------------------------
# Rendered-file checks: run these on the actual output
# --------------------------------------------------------------------------


def _ffmpeg() -> str:
    """Resolve `ffmpeg` through PATH, as OpenMontage's tools do."""
    try:
        return ffmpeg_path()
    except FFmpegNotAvailable as exc:
        raise TransitionAuditError(str(exc)) from exc


def frame_difference_profile(
    video: str | Path, at_seconds: float, window_seconds: float = 3.0
) -> list[float]:
    """Mean absolute frame difference per frame around a timestamp.

    Uses `scdet`, which reports `lavfi.scd.mafd` for every frame. A hard cut
    is one frame with a large value; a dissolve raises several frames slightly
    and produces no dominant spike.
    """
    start = max(0.0, at_seconds - window_seconds / 2.0)
    result = subprocess.run(
        [
            _ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{window_seconds:.3f}",
            "-i",
            str(video),
            "-vf",
            "scdet=threshold=0,metadata=print:file=-",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    stream = result.stdout + result.stderr
    return [
        float(v) for v in re.findall(r"lavfi\.scd\.mafd=(-?\d+(?:\.\d+)?)", stream)
    ]


@dataclass
class BoundaryObservation:
    """What the rendered file actually does at one approved boundary."""

    at_seconds: float
    approved_type: str
    approved_duration_seconds: float
    observed_type: str
    peak_mafd: float
    baseline_mafd: float
    spike_ratio: float

    @property
    def matches(self) -> bool:
        """Did the render deliver the approved kind of join?"""
        if self.approved_type in OVERLAPPING_TYPES:
            return self.observed_type == "dissolve"
        if self.approved_type in NON_OVERLAPPING_TYPES - {"cut"}:
            # A fade through black reads as a gradual change, not a spike.
            return self.observed_type != "cut"
        return self.observed_type == "cut"

    def describe(self) -> str:
        verdict = "matches" if self.matches else "MISMATCH"
        return (
            f"{self.at_seconds:.2f}s approved={self.approved_type}"
            f"({self.approved_duration_seconds:.2f}s) "
            f"observed={self.observed_type} "
            f"peak={self.peak_mafd:.1f} baseline={self.baseline_mafd:.1f} "
            f"ratio={self.spike_ratio:.1f}x -> {verdict}"
        )


def classify_boundary(profile: Sequence[float]) -> tuple[str, float, float, float]:
    """Classify an observed boundary from its frame-difference profile.

    Returns ``(observed_type, peak, baseline, ratio)`` where observed_type is
    ``cut``, ``dissolve`` or ``indeterminate``.
    """
    values = [v for v in profile if v > 0]
    if len(values) < 5:
        return "indeterminate", 0.0, 0.0, 0.0

    peak = max(values)
    ordered = sorted(values)
    baseline = ordered[len(ordered) // 2]
    ratio = peak / baseline if baseline > 0 else float("inf")

    if ratio >= CUT_SPIKE_RATIO and peak >= CUT_SPIKE_FLOOR:
        return "cut", round(peak, 2), round(baseline, 2), round(ratio, 2)
    return "dissolve", round(peak, 2), round(baseline, 2), round(ratio, 2)


def audit_rendered_boundaries(
    video: str | Path,
    transitions: Sequence[Transition],
    *,
    offset_seconds: float = 0.0,
    only_at: Optional[Sequence[float]] = None,
    window_seconds: float = 3.0,
) -> list[BoundaryObservation]:
    """Compare the rendered file against the approved transition map.

    `offset_seconds` accounts for a concatenated opening segment, so approved
    timeline positions line up with positions in the delivered file.

    `only_at` restricts the audit to selected boundaries — use it to check the
    chunk joins and a sample of interior joins rather than re-decoding a
    two-hour film at every one of several hundred boundaries.
    """
    video = Path(video)
    if not video.is_file():
        raise TransitionAuditError(f"no such render: {video}")

    selected = [t for t in transitions if t.at_seconds > 0]
    if only_at is not None:
        wanted = [round(a, 2) for a in only_at]
        selected = [
            t for t in selected if any(abs(t.at_seconds - w) < 0.05 for w in wanted)
        ]

    observations: list[BoundaryObservation] = []
    for transition in selected:
        at = transition.at_seconds + offset_seconds
        profile = frame_difference_profile(video, at, window_seconds)
        observed, peak, baseline, ratio = classify_boundary(profile)
        observations.append(
            BoundaryObservation(
                at_seconds=round(at, 3),
                approved_type=transition.type,
                approved_duration_seconds=transition.duration_seconds,
                observed_type=observed,
                peak_mafd=peak,
                baseline_mafd=baseline,
                spike_ratio=ratio,
            )
        )
    return observations


def unexpected_cuts(
    observations: Sequence[BoundaryObservation],
) -> list[BoundaryObservation]:
    """Approved dissolves that the render delivered as hard cuts.

    This is the exact Test 2 defect, and the check that would have caught it.
    """
    return [
        o
        for o in observations
        if o.approved_type in OVERLAPPING_TYPES and o.observed_type == "cut"
    ]


def audit_report(
    observations: Sequence[BoundaryObservation],
    violations: Sequence[ChunkBoundaryViolation] = (),
    discipline: Optional[TransitionDiscipline] = None,
) -> dict[str, Any]:
    """Shape for `render_report.qc.transitions`."""
    mismatches = [o for o in observations if not o.matches]
    return {
        "boundaries_inspected": len(observations),
        "boundaries_matching_approved_map": len(observations) - len(mismatches),
        "unexpected_hard_cuts": [o.describe() for o in unexpected_cuts(observations)],
        "mismatches": [o.describe() for o in mismatches],
        "chunk_plan_violations": [v.describe() for v in violations],
        "discipline": discipline.to_metadata() if discipline else None,
        "passed": not mismatches and not violations,
        "method": (
            "per-frame mean absolute difference (scdet) sampled around each "
            "approved boundary in the ENCODED file; a hard cut is a single-frame "
            f"spike at or above {CUT_SPIKE_RATIO}x the local baseline"
        ),
    }


def _main(argv: list[str]) -> int:
    """Audit a render against an edit_decisions checkpoint.

    python -m lib.transition_audit RENDER.mp4 checkpoint_edit.json [offset]
    """
    if len(argv) < 2:
        print(_main.__doc__)
        return 2

    render, checkpoint = argv[0], argv[1]
    offset = float(argv[2]) if len(argv) > 2 else 0.0

    data = json.loads(Path(checkpoint).read_text(encoding="utf-8"))
    edit = data.get("artifacts", {}).get("edit_decisions", data)
    transitions = as_transitions(edit.get("transitions", []))

    discipline = transition_discipline(transitions)
    observations = audit_rendered_boundaries(render, transitions, offset_seconds=offset)
    print(json.dumps(audit_report(observations, (), discipline), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
