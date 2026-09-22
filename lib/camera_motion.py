"""Measure CAMERA motion separately from SUBJECT motion in real footage.

A fixed camera filming a rushing river is not a moving-camera shot. Both
produce "motion" in a naive frame-difference measure, and the relaxation
pipeline needs to tell them apart: a channel that promises aerial, tracking
and gliding footage cannot satisfy that promise with locked-off ASMR water
close-ups, however energetically the water moves.

Why this module exists
----------------------
`tools/analysis/video_analyzer.py` already classifies scene motion as
`static_image | animated_still | motion_clip`. That answers a different
question — "is this real video or a pan over a still?" — and it imports
`cv2`, which is **not installed on this machine**, so on this installation it
returns `motion_type: "unknown"` for every clip. Nothing downstream ever
received camera-motion data. Hence a numpy + FFmpeg implementation with no
new third-party dependency.

How the two signals are separated
---------------------------------
Camera translation accumulates coherently over time; flowing water does not.
Over a one-second baseline a river's surface has completely decorrelated,
while rocks, banks and trees are still recognisable. So:

* **Camera motion** — the peak of a plain normalised cross-correlation
  between two frames one second apart. Static scene structure carries the
  peak; decorrelated froth only raises the noise floor. A displacement that
  is *consistent across several sampling windows* is camera movement.
  Quadrant displacements give divergence (push in / pull out) and let a
  tracking move be told from a pan.

* **Subject motion** — the residual after that global displacement is
  compensated, measured only over pixels with enough local texture to show
  motion at all. A flat grey sky cannot evidence either kind of movement, so
  it is excluded from the denominator rather than counted as "still".

Both are recorded. Neither substitutes for the other.

Units are resolution-independent: displacement is expressed as a fraction of
the frame per second, so a 1080p and a 4K clip of the same move measure the
same.

Honesty
-------
Every returned figure comes from decoded frames. The module samples windows
rather than whole clips, and says which windows it sampled. It does not
classify shot scale (wide/medium/detail) or season — those are director
judgements made by looking at frames, and `sample_frames()` exists to supply
them. A caller must not present this module's output as a shot-scale or
season assessment.

Known limitation
----------------
The camera measure asks "did a large, coherent part of the frame translate?"
So a **subject** that fills most of the frame and moves **rigidly** will be
reported as camera movement. Observed while building the tests: a synthetic
clip whose middle 64% was a rigidly scrolling strip of texture measured as a
leftward tracking move, on a camera that never moved.

Real flowing water does not do this — froth decorrelates frame to frame and
carries no coherent displacement, which is why locked-off river clips measure
as `static` here and were verified to. But a locked-off shot of something
large moving as one body (a sheet of falling water, a raft crossing frame,
dense drifting fog) can read as a move. Look at the frames before relying on
a single `is_moving_camera` for such a shot.

Usage
-----
    from lib.camera_motion import analyse_clip

    m = analyse_clip(r"D:\\...\\pexels_4318716.mp4")
    m.camera_motion          # 'tracking'
    m.camera_speed_band      # 'graceful'
    m.subject_motion         # 'strong'
    m.is_moving_camera       # True

CLI:

    python -m lib.camera_motion CLIP [CLIP ...] [--json]
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Analysis geometry
# --------------------------------------------------------------------------

#: Working resolution for the correlation. Small enough to be fast, large
#: enough that a slow move still produces several pixels of displacement over
#: the baseline.
PROBE_WIDTH = 320
PROBE_HEIGHT = 180

#: Frames decoded per second inside a sampling window.
PROBE_FPS = 4

#: Seconds between the two frames compared for camera displacement. Long
#: enough that water decorrelates, short enough that a real move stays inside
#: the correlation's unambiguous range.
BASELINE_SECONDS = 1.0

#: Sampling windows, as fractions of clip duration.
WINDOW_POSITIONS = (0.15, 0.45, 0.75)

# --------------------------------------------------------------------------
# Decision thresholds, in fraction-of-frame per second
# --------------------------------------------------------------------------

#: Below this the camera is doing nothing a viewer would read as movement.
#: A couple of pixels of encoder wobble at probe resolution sits under it.
STATIC_MAX = 0.010

#: Bands above STATIC_MAX. 'graceful' is the band this channel wants;
#: 'brisk' is acceptable only with a reason; 'aggressive' is a reject.
GRACEFUL_MAX = 0.080
BRISK_MAX = 0.150

#: A displacement only counts as camera motion if it points the same way in
#: most windows. Water swirl is not consistent; a drone is.
CONSISTENCY_MIN = 0.6

#: Divergence (quadrants spreading apart) at or above this reads as a
#: push-in / pull-out rather than a lateral move.
DIVERGENCE_MIN = 0.020

#: Per-step direction scatter above this reads as shake, not a steady move.
JITTER_MAX = 0.55

#: Fraction of textured pixels that must change for subject motion to count.
SUBJECT_STILL_MAX = 0.08
SUBJECT_MODERATE_MAX = 0.35

#: Correlation between the clip's opening and its tail, above which the clip
#: is *suspected* of being a loop. Suspicion, not proof — a locked-off shot of
#: a uniform scene also correlates highly with itself.
LOOP_SUSPICION_MIN = 0.90

_RAW_DIFF_THRESHOLD = 5.0  # 8-bit levels; below this a pixel has not moved
_TEXTURE_THRESHOLD = 6.0  # local gradient needed to evidence motion at all
_MIN_TEXTURED_PIXELS = 400


class CameraMotionError(RuntimeError):
    """Raised when a clip cannot be decoded for analysis."""


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass
class WindowMeasurement:
    """One sampling window's raw numbers. Kept so a claim can be audited."""

    at_seconds: float
    dx_per_second: float
    dy_per_second: float
    displacement_per_second: float
    divergence_per_second: float
    aligned_correlation: float
    unaligned_correlation: float
    subject_moving_fraction: float
    subject_residual_energy: float


@dataclass
class ClipMotion:
    """What a clip actually contains, camera and subject recorded apart."""

    path: str
    duration_seconds: float

    # --- camera ---
    camera_motion: str  # static | drift | pan | tilt | tracking | push_in | pull_out
    camera_direction: Optional[str]  # left|right|up|down|in|out, None when static
    camera_speed_band: str  # still | graceful | brisk | aggressive
    camera_displacement_per_second: float
    camera_consistency: float
    camera_steadiness: str  # steady | slightly_unsteady | shaky

    # --- subject, measured independently ---
    subject_motion: str  # still | gentle | moderate | strong
    subject_moving_fraction: float

    # --- screening ---
    loop_suspected: bool
    loop_correlation: float

    windows: list[WindowMeasurement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_moving_camera(self) -> bool:
        """True only for genuine camera movement.

        A locked-off camera on white water is emphatically False, which is the
        whole point of this module.
        """
        return self.camera_motion not in ("static", "drift")

    @property
    def is_relaxation_suitable_movement(self) -> bool:
        """Moving, and moving in a way this format can actually use."""
        return (
            self.is_moving_camera
            and self.camera_speed_band in ("graceful", "brisk")
            and self.camera_steadiness == "steady"
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_moving_camera"] = self.is_moving_camera
        d["is_relaxation_suitable_movement"] = self.is_relaxation_suitable_movement
        return d

    def summary(self) -> str:
        """One line for an asset manifest's provenance note."""
        cam = self.camera_motion
        if self.camera_direction:
            cam = f"{cam}_{self.camera_direction}"
        return (
            f"camera={cam} ({self.camera_speed_band}, {self.camera_steadiness}, "
            f"{self.camera_displacement_per_second:.3f} frame/s, "
            f"consistency={self.camera_consistency:.2f}); "
            f"subject={self.subject_motion} "
            f"({self.subject_moving_fraction:.2f} of textured area)"
            + ("; LOOP SUSPECTED" if self.loop_suspected else "")
        )


# --------------------------------------------------------------------------
# FFmpeg plumbing
# --------------------------------------------------------------------------


def _ffmpeg_dir() -> Optional[Path]:
    """Prefer the pinned 7.1.1 build this installation standardises on."""
    pinned = Path(r"D:\VidQwik AI\Tools\ffmpeg-7.1.1-full_build\bin")
    if (pinned / "ffmpeg.exe").is_file():
        return pinned
    env = os.environ.get("OPENMONTAGE_FFMPEG_DIR")
    if env and (Path(env) / "ffmpeg.exe").is_file():
        return Path(env)
    found = shutil.which("ffmpeg")
    return Path(found).parent if found else None


def _binary(name: str) -> str:
    directory = _ffmpeg_dir()
    if directory is None:
        raise CameraMotionError(
            "ffmpeg not found; set OPENMONTAGE_FFMPEG_DIR or put ffmpeg on PATH"
        )
    exe = directory / (f"{name}.exe" if os.name == "nt" else name)
    return str(exe if exe.is_file() else directory / name)


def probe_duration(path: str | Path) -> float:
    """Container duration in seconds."""
    out = subprocess.run(
        [
            _binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise CameraMotionError(f"cannot read duration of {path}: {exc}") from exc


def _decode_grey(path: str | Path, start: float, duration: float):
    """Decode a window as greyscale float32 frames at probe resolution."""
    import numpy as np

    cmd = [
        _binary("ffmpeg"),
        "-v",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        f"fps={PROBE_FPS},scale={PROBE_WIDTH}:{PROBE_HEIGHT}:flags=area,format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    raw = subprocess.run(cmd, capture_output=True).stdout
    stride = PROBE_WIDTH * PROBE_HEIGHT
    count = len(raw) // stride
    if count == 0:
        return np.zeros((0, PROBE_HEIGHT, PROBE_WIDTH), dtype="float32")
    return (
        np.frombuffer(raw[: count * stride], dtype="uint8")
        .reshape(count, PROBE_HEIGHT, PROBE_WIDTH)
        .astype("float32")
    )


def sample_frames(
    path: str | Path,
    destination: str | Path,
    positions: Sequence[float] = (0.1, 0.35, 0.6, 0.85),
    width: int = 480,
) -> list[Path]:
    """Write sampled JPEG frames so a director can judge scale and season.

    This module deliberately does not guess those from statistics. Looking at
    the frames is the honest way to establish them.
    """
    duration = probe_duration(path)
    out_dir = Path(destination)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    stem = Path(path).stem
    for index, fraction in enumerate(positions):
        at = max(0.0, min(duration - 0.1, duration * fraction))
        target = out_dir / f"{stem}_f{index}_{at:.1f}s.jpg"
        subprocess.run(
            [
                _binary("ffmpeg"),
                "-v",
                "error",
                "-ss",
                f"{at:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                "-y",
                str(target),
            ],
            capture_output=True,
        )
        if target.is_file():
            written.append(target)
    return written


# --------------------------------------------------------------------------
# Correlation primitives
# --------------------------------------------------------------------------


def _ncc_shift_subpixel(a, b) -> tuple[float, float, float, float]:
    """`_ncc_shift`, refined to sub-pixel precision."""
    import numpy as np

    height, width = a.shape
    window = np.outer(np.hanning(height), np.hanning(width)).astype("float32")
    fa = (a - a.mean()) * window
    fb = (b - b.mean()) * window
    fa = fa / (float(np.sqrt((fa**2).sum())) or 1.0)
    fb = fb / (float(np.sqrt((fb**2).sum())) or 1.0)
    corr = np.fft.irfft2(np.fft.rfft2(fa) * np.conj(np.fft.rfft2(fb)), s=a.shape)
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    offset_y, offset_x = _subpixel_peak(corr, int(iy), int(ix))
    dy = (iy - height if iy > height // 2 else iy) + offset_y
    dx = (ix - width if ix > width // 2 else ix) + offset_x
    return float(dy), float(dx), float(corr.max()), float(corr[0, 0])


def _ncc_shift(a, b) -> tuple[int, int, float, float]:
    """Plain normalised cross-correlation peak between two frames.

    Returns ``(dy, dx, peak, zero_shift)``. Unlike phase correlation this is
    not spectrally whitened, which matters here: whitening amplifies the
    river's broadband froth until it competes with the scene structure and the
    peak collapses toward zero. Measured on a real aerial clip, whitened
    correlation reported 1 px of movement where the rocks had in fact
    traversed 12 px.

    ``zero_shift`` is the correlation without any alignment. When it is close
    to ``peak`` the frame did not move.
    """
    import numpy as np

    height, width = a.shape
    window = np.outer(np.hanning(height), np.hanning(width)).astype("float32")
    fa = (a - a.mean()) * window
    fb = (b - b.mean()) * window
    fa = fa / (float(np.sqrt((fa**2).sum())) or 1.0)
    fb = fb / (float(np.sqrt((fb**2).sum())) or 1.0)
    corr = np.fft.irfft2(np.fft.rfft2(fa) * np.conj(np.fft.rfft2(fb)), s=a.shape)
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy = int(iy - height) if iy > height // 2 else int(iy)
    dx = int(ix - width) if ix > width // 2 else int(ix)
    return dy, dx, float(corr.max()), float(corr[0, 0])


def _subpixel_peak(corr, iy: int, ix: int) -> tuple[float, float]:
    """Refine a correlation peak to sub-pixel precision.

    Fits a parabola through the peak and its two neighbours on each axis.
    Needed because camera displacement between two decoded frames is almost
    never a whole number of pixels, and an integer-only alignment leaves a
    residual that a naive subject-motion measure would blame on the subject.
    """
    height, width = corr.shape

    def refine(back: float, centre: float, forward: float) -> float:
        denominator = back - 2.0 * centre + forward
        if denominator == 0:
            return 0.0
        return max(-0.5, min(0.5, 0.5 * (back - forward) / denominator))

    dy = refine(
        float(corr[(iy - 1) % height, ix]),
        float(corr[iy, ix]),
        float(corr[(iy + 1) % height, ix]),
    )
    dx = refine(
        float(corr[iy, (ix - 1) % width]),
        float(corr[iy, ix]),
        float(corr[iy, (ix + 1) % width]),
    )
    return dy, dx


def _shift_bilinear(frame, dy: float, dx: float):
    """Translate a frame by a fractional offset, bilinearly."""
    import numpy as np

    floor_y, floor_x = math.floor(dy), math.floor(dx)
    frac_y, frac_x = dy - floor_y, dx - floor_x

    def rolled(oy: int, ox: int):
        return np.roll(np.roll(frame, oy, axis=0), ox, axis=1)

    return (
        rolled(floor_y, floor_x) * (1 - frac_y) * (1 - frac_x)
        + rolled(floor_y + 1, floor_x) * frac_y * (1 - frac_x)
        + rolled(floor_y, floor_x + 1) * (1 - frac_y) * frac_x
        + rolled(floor_y + 1, floor_x + 1) * frac_y * frac_x
    )


def _smooth(frame):
    """Mild 3x3 box blur.

    Applied before the residual comparison only. Sub-pixel resampling error
    lives in the highest spatial frequencies, so smoothing suppresses it while
    leaving genuine subject displacement plainly visible. Synthetic test
    patterns with hard edges are the worst case for this and the reason it is
    here.
    """
    import numpy as np

    padded = np.pad(frame, 1, mode="edge")
    return (
        padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:]
        + padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:]
        + padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]
    ) / 9.0


def _texture_mask(frame):
    """Pixels with enough local gradient to evidence motion."""
    import numpy as np

    gy = np.abs(np.diff(frame, axis=0, prepend=frame[:1]))
    gx = np.abs(np.diff(frame, axis=1, prepend=frame[:, :1]))
    return (gy + gx) > _TEXTURE_THRESHOLD


def _quadrant_divergence(a, b) -> float:
    """Outward spread of the four quadrants, in pixels.

    Positive means the frame's contents are expanding away from the centre —
    a push in. Negative means contracting — a pull out.
    """
    half_h, half_w = PROBE_HEIGHT // 2, PROBE_WIDTH // 2
    quads = {
        "tl": (slice(0, half_h), slice(0, half_w)),
        "tr": (slice(0, half_h), slice(half_w, PROBE_WIDTH)),
        "bl": (slice(half_h, PROBE_HEIGHT), slice(0, half_w)),
        "br": (slice(half_h, PROBE_HEIGHT), slice(half_w, PROBE_WIDTH)),
    }
    shifts = {k: _ncc_shift(a[s], b[s])[:2] for k, s in quads.items()}
    spread_x = (
        (shifts["tr"][1] + shifts["br"][1]) - (shifts["tl"][1] + shifts["bl"][1])
    ) / 2.0
    spread_y = (
        (shifts["bl"][0] + shifts["br"][0]) - (shifts["tl"][0] + shifts["tr"][0])
    ) / 2.0
    return (spread_x / PROBE_WIDTH + spread_y / PROBE_HEIGHT) / 2.0


# --------------------------------------------------------------------------
# Per-window measurement
# --------------------------------------------------------------------------


def _measure_window(frames, at_seconds: float) -> Optional[WindowMeasurement]:
    import numpy as np

    step = int(round(BASELINE_SECONDS * PROBE_FPS))
    if len(frames) <= step:
        return None

    dxs: list[float] = []
    dys: list[float] = []
    peaks: list[float] = []
    zeros: list[float] = []
    divergences: list[float] = []

    for i in range(len(frames) - step):
        a, b = frames[i], frames[i + step]
        dy, dx, peak, zero = _ncc_shift(a, b)
        dys.append(dy / PROBE_HEIGHT / BASELINE_SECONDS)
        dxs.append(dx / PROBE_WIDTH / BASELINE_SECONDS)
        peaks.append(peak)
        zeros.append(zero)
        divergences.append(_quadrant_divergence(a, b) / BASELINE_SECONDS)

    if not dxs:
        return None

    # Subject motion is measured over ADJACENT frames, so the river is not
    # credited to the camera and the camera is not credited to the river.
    #
    # Alignment is applied only when the camera is ACTUALLY moving, judged
    # from this window's own median displacement. Two reasons:
    #
    #  * On a locked-off shot the correlation peak between adjacent frames of
    #    turbulent water is noise. "Correcting" for it invents a sub-pixel
    #    shift, and the bilinear resampling that follows attenuates exactly
    #    the high-frequency change being measured - it took a rushing-stream
    #    close-up from 0.85 to 0.24 of its textured area.
    #  * A correction that cannot be measured reliably should not be applied.
    camera_is_moving = (
        float(np.hypot(np.median(dxs), np.median(dys))) > STATIC_MAX
    )

    moving: list[float] = []
    residual: list[float] = []
    for i in range(len(frames) - 1):
        a, b = frames[i], frames[i + 1]
        mask = _texture_mask(a)
        if int(mask.sum()) < _MIN_TEXTURED_PIXELS:
            continue

        if camera_is_moving:
            dy, dx, _, _ = _ncc_shift_subpixel(a, b)
            # NOTE the sign. The correlation peak gives the displacement of
            # the frame CONTENTS; bringing `a` into register with `b` means
            # shifting it back the other way. Verified on a synthetic pan:
            # aligning with -d leaves a residual of 0.14 grey levels, +d
            # leaves 84.9 - worse than not aligning at all (34.5). Getting
            # this backwards charges every camera move to the subject, which
            # is the conflation this module exists to prevent.
            aligned = _shift_bilinear(a, -dy, -dx)
            # Smoothing suppresses the bilinear resampling error, which lives
            # in the top spatial frequencies.
            compare_b, compare_a = _smooth(b), _smooth(aligned)
            pad = int(max(abs(dy), abs(dx))) + 2
        else:
            compare_b, compare_a = b, a
            pad = 2

        core = (
            slice(pad, PROBE_HEIGHT - pad),
            slice(pad, PROBE_WIDTH - pad),
        )
        core_mask = mask[core]
        if int(core_mask.sum()) < _MIN_TEXTURED_PIXELS // 4:
            continue
        diff = np.abs(compare_b[core] - compare_a[core])
        moving.append(float((diff[core_mask] >= _RAW_DIFF_THRESHOLD).mean()))
        residual.append(float(diff[core_mask].mean()))

    return WindowMeasurement(
        at_seconds=round(at_seconds, 2),
        dx_per_second=round(float(np.median(dxs)), 5),
        dy_per_second=round(float(np.median(dys)), 5),
        displacement_per_second=round(
            float(np.hypot(np.median(dxs), np.median(dys))), 5
        ),
        divergence_per_second=round(float(np.median(divergences)), 5),
        aligned_correlation=round(float(np.median(peaks)), 4),
        unaligned_correlation=round(float(np.median(zeros)), 4),
        subject_moving_fraction=round(float(np.median(moving)), 4) if moving else 0.0,
        subject_residual_energy=(
            round(float(np.median(residual)), 3) if residual else 0.0
        ),
    )


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def _speed_band(displacement: float) -> str:
    if displacement < STATIC_MAX:
        return "still"
    if displacement < GRACEFUL_MAX:
        return "graceful"
    if displacement < BRISK_MAX:
        return "brisk"
    return "aggressive"


def _subject_band(fraction: float) -> str:
    if fraction < SUBJECT_STILL_MAX:
        return "still"
    if fraction < SUBJECT_MODERATE_MAX:
        return "gentle"
    if fraction < 0.70:
        return "moderate"
    return "strong"


def _classify_camera(
    windows: list[WindowMeasurement],
) -> tuple[str, Optional[str], float, float, str]:
    """Return (motion, direction, displacement, consistency, steadiness)."""
    import numpy as np

    dxs = np.array([w.dx_per_second for w in windows])
    dys = np.array([w.dy_per_second for w in windows])
    divs = np.array([w.divergence_per_second for w in windows])

    median_dx = float(np.median(dxs))
    median_dy = float(np.median(dys))
    displacement = float(np.hypot(median_dx, median_dy))
    divergence = float(np.median(divs))

    # Consistency: does the displacement point the same way every window?
    if len(windows) > 1 and displacement > 0:
        unit = np.array([median_dx, median_dy]) / displacement
        projections = [
            float(np.dot([dx, dy], unit)) for dx, dy in zip(dxs, dys)
        ]
        magnitudes = [float(np.hypot(dx, dy)) for dx, dy in zip(dxs, dys)]
        agreeing = [
            p / m for p, m in zip(projections, magnitudes) if m > STATIC_MAX / 2
        ]
        consistency = float(np.mean(agreeing)) if agreeing else 0.0
    else:
        consistency = 1.0 if displacement > 0 else 0.0

    # Steadiness: scatter of the per-window displacement magnitude.
    magnitudes = [float(np.hypot(dx, dy)) for dx, dy in zip(dxs, dys)]
    mean_magnitude = float(np.mean(magnitudes))
    if mean_magnitude <= STATIC_MAX:
        steadiness = "steady"
    else:
        scatter = float(np.std(magnitudes)) / mean_magnitude
        steadiness = (
            "steady"
            if scatter < 0.30
            else "slightly_unsteady"
            if scatter < JITTER_MAX
            else "shaky"
        )

    lateral_static = displacement < STATIC_MAX
    zoom_only = abs(divergence) >= DIVERGENCE_MIN

    if lateral_static and not zoom_only:
        return "static", None, displacement, consistency, steadiness

    if lateral_static and zoom_only:
        motion = "push_in" if divergence > 0 else "pull_out"
        return (
            motion,
            "in" if divergence > 0 else "out",
            abs(divergence),
            consistency,
            steadiness,
        )

    if consistency < CONSISTENCY_MIN:
        # It moved, but not in an agreed direction: drift or swirl, not a move.
        return "drift", None, displacement, consistency, steadiness

    if zoom_only and abs(divergence) > displacement:
        motion = "push_in" if divergence > 0 else "pull_out"
        return (
            motion,
            "in" if divergence > 0 else "out",
            abs(divergence),
            consistency,
            steadiness,
        )

    # A lateral move. Divergence alongside it means the camera is travelling
    # through the scene rather than pivoting on the spot.
    if zoom_only:
        motion = "tracking"
    elif abs(median_dx) >= abs(median_dy):
        motion = "pan"
    else:
        motion = "tilt"

    if abs(median_dx) >= abs(median_dy):
        # Frame contents moving left means the camera swung right.
        direction = "right" if median_dx < 0 else "left"
    else:
        direction = "down" if median_dy < 0 else "up"

    return motion, direction, displacement, consistency, steadiness


def _loop_correlation(path: str | Path, duration: float) -> float:
    """Correlate the opening second against the closing second.

    High values *suggest* a looped clip. They also occur for a locked-off shot
    of an unchanging scene, so this is reported as suspicion and never as a
    finding on its own.
    """
    if duration < 4.0:
        return 0.0
    head = _decode_grey(path, 0.3, 1.0)
    tail = _decode_grey(path, max(0.3, duration - 1.3), 1.0)
    if len(head) == 0 or len(tail) == 0:
        return 0.0
    return _ncc_shift(head[0], tail[-1])[2]


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def analyse_clip(
    path: str | Path,
    window_positions: Sequence[float] = WINDOW_POSITIONS,
) -> ClipMotion:
    """Measure camera motion and subject motion in a real clip.

    Raises `CameraMotionError` when nothing could be decoded. A caller must
    treat that as "unknown", never as "static" — an unanalysed clip has not
    been screened.
    """
    path = Path(path)
    if not path.is_file():
        raise CameraMotionError(f"no such clip: {path}")

    duration = probe_duration(path)
    span = 2 * BASELINE_SECONDS + 0.5
    notes: list[str] = []

    windows: list[WindowMeasurement] = []
    for fraction in window_positions:
        start = max(0.3, duration * fraction)
        if start + span > duration:
            continue
        frames = _decode_grey(path, start, span)
        measurement = _measure_window(frames, start)
        if measurement is not None:
            windows.append(measurement)

    if not windows:
        # Short clip: fall back to one window from the top.
        frames = _decode_grey(path, 0.0, min(duration, span))
        measurement = _measure_window(frames, 0.0)
        if measurement is None:
            raise CameraMotionError(
                f"could not decode enough frames to analyse {path.name} "
                f"(duration {duration:.2f}s)"
            )
        windows.append(measurement)
        notes.append(
            "clip too short for spaced sampling; measured one window from the start"
        )

    if len(windows) < 2:
        notes.append(
            "only one sampling window available - camera-motion consistency is "
            "not established across the clip"
        )

    motion, direction, displacement, consistency, steadiness = _classify_camera(windows)

    import numpy as np

    subject_fraction = float(
        np.median([w.subject_moving_fraction for w in windows])
    )

    loop_corr = _loop_correlation(path, duration)
    loop_suspected = loop_corr >= LOOP_SUSPICION_MIN and motion == "static"
    if loop_suspected:
        notes.append(
            f"opening and closing frames correlate at {loop_corr:.2f} on a "
            "locked-off camera - inspect for a loop before use"
        )

    return ClipMotion(
        path=str(path),
        duration_seconds=round(duration, 2),
        camera_motion=motion,
        camera_direction=direction,
        camera_speed_band=_speed_band(displacement),
        camera_displacement_per_second=round(displacement, 5),
        camera_consistency=round(consistency, 3),
        camera_steadiness=steadiness,
        subject_motion=_subject_band(subject_fraction),
        subject_moving_fraction=round(subject_fraction, 4),
        loop_suspected=loop_suspected,
        loop_correlation=round(loop_corr, 4),
        windows=windows,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Pool-level screening
# --------------------------------------------------------------------------


def movement_profile(measurements: Sequence[ClipMotion]) -> dict[str, Any]:
    """Summarise a footage pool's camera-movement variety.

    The relaxation Scene Director uses this to answer the question Test 2 got
    wrong: does this pool support a film with a sense of travel, or is it a
    stack of locked-off water close-ups?
    """
    total = len(measurements)
    if total == 0:
        return {"clips": 0, "moving_camera_share": 0.0, "by_camera_motion": {}}

    counts: dict[str, int] = {}
    for m in measurements:
        counts[m.camera_motion] = counts.get(m.camera_motion, 0) + 1

    moving = [m for m in measurements if m.is_moving_camera]
    usable_moving = [m for m in measurements if m.is_relaxation_suitable_movement]
    locked_water = [
        m
        for m in measurements
        if not m.is_moving_camera and m.subject_motion in ("moderate", "strong")
    ]

    return {
        "clips": total,
        "by_camera_motion": counts,
        "moving_camera_clips": len(moving),
        "moving_camera_share": round(len(moving) / total, 3),
        "usable_moving_camera_clips": len(usable_moving),
        "usable_moving_camera_share": round(len(usable_moving) / total, 3),
        "locked_off_moving_water_clips": len(locked_water),
        "locked_off_moving_water_share": round(len(locked_water) / total, 3),
        "distinct_camera_motions": sorted(counts),
        "loop_suspected_clips": [
            Path(m.path).name for m in measurements if m.loop_suspected
        ],
    }


def _main(argv: list[str]) -> int:
    as_json = "--json" in argv
    clips = [a for a in argv if not a.startswith("--")]
    if not clips:
        print(__doc__)
        return 2

    results: list[ClipMotion] = []
    for clip in clips:
        try:
            measurement = analyse_clip(clip)
        except CameraMotionError as exc:
            print(f"{Path(clip).name}: UNANALYSED - {exc}")
            continue
        results.append(measurement)
        if not as_json:
            print(f"{Path(clip).name}: {measurement.summary()}")

    if as_json:
        print(
            json.dumps(
                {
                    "clips": [r.to_dict() for r in results],
                    "profile": movement_profile(results),
                },
                indent=2,
            )
        )
    elif len(results) > 1:
        print()
        print(json.dumps(movement_profile(results), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
