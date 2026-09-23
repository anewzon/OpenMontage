"""Build long ambience beds from short loopable sources - and check every seam.

Generated ambience (water, rain, wind, room tone) arrives as short sources,
and a bed of any length is built by repeating them. Two things go wrong at
the joins, and both were measured on this installation:

* **Quiet edges.** A generated loop's first and last ~0.1 s sit 3-4 dB under
  its body, so butting passes together leaves an audible dip at every seam.
  The edges are trimmed first.
* **Linear crossfades dip.** Uncorrelated sources (which ambience is) lose
  ~3 dB in the middle of a linear crossfade. Joins use an equal-power
  (quarter-sine) crossfade, which keeps the power constant.

The method: trim the edges, fold the source's tail into its head with an
equal-power crossfade so the resulting unit ends exactly where it begins,
then repeat the unit. Every repetition point and every crossfade centre is
recorded, and `seam_report` compares the short-term level at each one with
the bed just around it. A bed whose seams deviate more than the threshold is
not approved.

Nothing here knows a channel or a sound category.

    from lib.ambience_loop import build_loop_bed, seam_report
    bed = build_loop_bed("assets/audio/bed_a.mp3", "work/stems/bed_a.wav", 7200)
    report = seam_report(bed["path"], bed["seam_seconds"])
    report["passed"]            # False blocks approval of the bed
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Sequence

from lib.ffmpeg_runtime import ffmpeg_path, ffprobe_path

__all__ = ["LoopBedError", "build_loop_bed", "edge_levels", "seam_report"]

#: Default seconds trimmed from each end of a generated source.
EDGE_TRIM_SECONDS = 0.5
#: Default equal-power crossfade folding the tail into the head.
CROSSFADE_SECONDS = 2.0
#: A seam whose short-term level strays more than this from the bed around it
#: is audible as a bump or a dip.
SEAM_MAX_DEVIATION_DB = 1.5
_ANALYSIS_RATE = 8000
_FRAME_SECONDS = 0.1


class LoopBedError(RuntimeError):
    """A loop bed cannot be built safely from this source."""


def _duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe_path(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _rms_frames(path: Path) -> list[float]:
    """Short-term RMS in dBFS, one value per 100 ms, of the mono mix."""
    import numpy as np

    raw = subprocess.run(
        [ffmpeg_path(), "-v", "error", "-i", str(path), "-ac", "1", "-ar",
         str(_ANALYSIS_RATE), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    size = int(_ANALYSIS_RATE * _FRAME_SECONDS)
    count = len(samples) // size
    frames = samples[: count * size].reshape(count, size)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return [20 * math.log10(max(v, 1e-9)) for v in rms]


def edge_levels(path: str | Path, edge_seconds: float = 0.1) -> dict[str, float]:
    """How much quieter a source's first and last ``edge_seconds`` are than its body."""
    frames = _rms_frames(Path(path))
    n = max(1, int(round(edge_seconds / _FRAME_SECONDS)))
    if len(frames) < 4 * n:
        raise LoopBedError(f"{Path(path).name} is too short to measure its edges")
    body = sorted(frames[n:-n])[len(frames[n:-n]) // 2]
    head = sum(frames[:n]) / n
    tail = sum(frames[-n:]) / n
    return {"head_vs_body_db": round(head - body, 2), "tail_vs_body_db": round(tail - body, 2),
            "body_rms_dbfs": round(body, 2)}


def build_loop_bed(
    source: str | Path,
    output: str | Path,
    duration_seconds: float,
    *,
    edge_trim_seconds: float = EDGE_TRIM_SECONDS,
    crossfade_seconds: float = CROSSFADE_SECONDS,
    sample_rate: int = 48000,
) -> dict[str, Any]:
    """Build a bed of ``duration_seconds`` from one loopable source.

    Returns ``{"path", "unit_seconds", "seam_seconds", "edge_trim_seconds",
    "crossfade_seconds", "source_edges"}``. ``seam_seconds`` lists every
    repetition join and crossfade centre in the bed - pass it to `seam_report`.
    """
    source, output = Path(source), Path(output)
    length = _duration(source)
    usable = length - 2 * edge_trim_seconds
    if crossfade_seconds <= 0 or edge_trim_seconds < 0:
        raise LoopBedError("crossfade must be positive and the edge trim non-negative")
    if usable < 3 * crossfade_seconds:
        raise LoopBedError(
            f"{source.name} is {length:.2f} s; after trimming {edge_trim_seconds} s edges it "
            f"is too short for a {crossfade_seconds} s crossfade")
    unit = usable - crossfade_seconds
    x, e = crossfade_seconds, edge_trim_seconds
    # S = trimmed source (len L). Unit U = [tail of S crossfaded into head of S]
    # + [S from X to L-X]; U's last sample continues into its first, so the
    # unit repeats without a join of its own.
    graph = (
        f"[0:a]aresample={sample_rate},atrim=start={e}:end={e + usable},asetpts=N/SR/TB,"
        "asplit=3[s1][s2][s3];"
        f"[s1]atrim=start={usable - x}:end={usable},asetpts=N/SR/TB[tail];"
        f"[s2]atrim=start=0:end={x},asetpts=N/SR/TB[head];"
        f"[s3]atrim=start={x}:end={usable - x},asetpts=N/SR/TB[mid];"
        f"[tail][head]acrossfade=d={x}:c1=qsin:c2=qsin[fold];"
        "[fold][mid]concat=n=2:v=0:a=1[unit];"
        f"[unit]aloop=loop=-1:size={int(round(unit * sample_rate))},"
        f"atrim=end={duration_seconds},asetpts=N/SR/TB[out]"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg_path(), "-y", "-v", "error", "-i", str(source), "-filter_complex", graph,
         "-map", "[out]", "-ar", str(sample_rate), str(output)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise LoopBedError(f"ffmpeg could not build the bed: {result.stderr.strip()[:400]}")
    # Check both the repetition joins and the middle of every crossfade (a
    # linear fade would dip there; equal-power should not).
    points = set()
    for k in range(int(duration_seconds // unit) + 1):
        start = k * unit
        candidates = [start + x / 2] + ([start] if k else [])
        points.update(round(t, 3) for t in candidates if 0 < t < duration_seconds - 0.05)
    seams = sorted(points)
    return {"path": str(output), "unit_seconds": round(unit, 3), "seam_seconds": seams,
            "edge_trim_seconds": edge_trim_seconds, "crossfade_seconds": crossfade_seconds,
            "crossfade_curve": "equal-power (qsin)", "source_edges": edge_levels(source)}


def seam_report(
    path: str | Path,
    seam_seconds: Sequence[float],
    *,
    max_deviation_db: float = SEAM_MAX_DEVIATION_DB,
    centre_seconds: float = 0.3,
    exclude_seconds: float = 1.2,
    reference_seconds: float = 3.0,
) -> dict[str, Any]:
    """Short-term level at every seam against the bed just around it.

    At each seam the median level of the 100 ms frames within ``centre_seconds``
    is compared with the median of the frames between ``exclude_seconds`` and
    ``reference_seconds`` on either side. Medians on both sides: a mean of a
    fluctuating signal's frame levels is biased against a median, and a local
    reference keeps a bed's natural slow movement from reading as a seam. ``passed`` is False when any
    seam strays more than ``max_deviation_db``.
    """
    frames = _rms_frames(Path(path))
    if not frames:
        raise LoopBedError(f"{Path(path).name} has no measurable audio")

    def index(t: float) -> int:
        return int(round(t / _FRAME_SECONDS))

    seams = []
    for at in seam_seconds:
        centre = frames[max(0, index(at - centre_seconds)): index(at + centre_seconds) + 1]
        around = (frames[max(0, index(at - reference_seconds)): max(0, index(at - exclude_seconds))]
                  + frames[index(at + exclude_seconds): index(at + reference_seconds) + 1])
        if not centre or len(around) < 4:
            continue
        reference = sorted(around)[len(around) // 2]
        deviation = sorted(centre)[len(centre) // 2] - reference
        seams.append({"at_seconds": at, "deviation_db": round(deviation, 2),
                      "passed": abs(deviation) <= max_deviation_db})
    worst = max((abs(s["deviation_db"]) for s in seams), default=0.0)
    return {"max_deviation_db": max_deviation_db, "worst_seam_deviation_db": round(worst, 2),
            "seams": seams, "passed": bool(seams) and all(s["passed"] for s in seams),
            "measured": ("median 100 ms RMS of the mono mix at each seam against the median "
                         "of the bed around it")}
