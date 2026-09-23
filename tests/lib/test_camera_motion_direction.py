"""Camera-motion DIRECTION and steadiness, against synthetic clips of known motion.

Each clip is a virtual camera over a large still texture: the crop window
moves right (a pan right), down (a tilt down), shrinks (a push in) or grows
(a pull out). The frame CONTENTS move the opposite way to the camera, and the
analyser must report the camera's direction. The audit found every direction
reported backwards (a sign error between the correlation peak and the content
displacement); these tests pin it.

The water fixture replaces most of each frame with fresh noise, the way white
water decorrelates between frames. A moving camera over it must not be
labelled shaky merely because the correlation is too weak to measure.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from lib.camera_motion import analyse_clip  # noqa: E402

W, H, FPS, SECONDS = 320, 180, 24, 8

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _texture(rng, height, width):
    """Smooth, strongly structured texture: correlates well when it moves."""
    base = rng.random((height // 8 + 2, width // 8 + 2))
    big = np.kron(base, np.ones((8, 8)))[:height, :width]
    fine = rng.random((height, width)) * 0.35
    img = big * 0.65 + fine
    return (img / img.max() * 235 + 10).astype(np.float32)


def _crop(texture, cx, cy, scale):
    """The camera's view: a crop centred at (cx, cy), `scale` of W x H, resized."""
    cw, ch = W * scale, H * scale
    xs = cx - cw / 2 + (np.arange(W) + 0.5) * (cw / W)
    ys = cy - ch / 2 + (np.arange(H) + 0.5) * (ch / H)
    xi = np.clip(xs.astype(int), 0, texture.shape[1] - 1)
    yi = np.clip(ys.astype(int), 0, texture.shape[0] - 1)
    return texture[np.ix_(yi, xi)]


def _write(path: Path, frames) -> Path:
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
         "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(path)],
        stdin=subprocess.PIPE)
    for frame in frames:
        proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def _camera_clip(path, *, dx=0.0, dy=0.0, zoom=0.0, water=0.0, seed=7):
    """dx/dy: camera travel in frame-widths/heights per second; zoom: scale change per s.

    Positive dx = camera moves RIGHT; positive dy = camera moves DOWN; positive
    zoom = the view WIDENS (pull out), negative = it narrows (push in).
    `water` replaces that fraction of each frame (a central band) with fresh
    noise every frame.
    """
    rng = np.random.default_rng(seed)
    texture = _texture(rng, H * 4, W * 4)
    n = FPS * SECONDS
    frames = []
    band = int(H * water)
    top = (H - band) // 2
    for i in range(n):
        t = i / FPS
        cx = W * 2 + dx * W * (t - SECONDS / 2)
        cy = H * 2 + dy * H * (t - SECONDS / 2)
        scale = 1.6 + zoom * (t - SECONDS / 2)
        frame = _crop(texture, cx, cy, scale)
        if band:
            frame[top:top + band] = rng.random((band, W)) * 245 + 5
        frames.append(frame)
    return _write(path, frames)


@pytest.mark.parametrize("dx,dy,motion,direction", [
    (+0.05, 0.0, "pan", "right"),
    (-0.05, 0.0, "pan", "left"),
    (0.0, +0.05, "tilt", "down"),
    (0.0, -0.05, "tilt", "up"),
])
def test_pan_and_tilt_report_the_cameras_direction(tmp_path, dx, dy, motion, direction):
    clip = _camera_clip(tmp_path / f"{direction}.mp4", dx=dx, dy=dy)
    m = analyse_clip(clip)
    assert (m.camera_motion, m.camera_direction) == (motion, direction), m.summary()


@pytest.mark.parametrize("zoom,motion,direction", [
    (-0.12, "push_in", "in"),
    (+0.12, "pull_out", "out"),
])
def test_push_in_and_pull_out_are_not_swapped(tmp_path, zoom, motion, direction):
    clip = _camera_clip(tmp_path / f"{motion}.mp4", zoom=zoom)
    m = analyse_clip(clip)
    assert (m.camera_motion, m.camera_direction) == (motion, direction), m.summary()


def test_window_displacement_is_the_contents_displacement(tmp_path):
    """A pan right moves the frame contents LEFT: dx_per_second is negative."""
    m = analyse_clip(_camera_clip(tmp_path / "right.mp4", dx=+0.05))
    assert all(w.dx_per_second < 0 for w in m.windows), [w.dx_per_second for w in m.windows]


def test_a_steady_pan_over_structure_is_steady(tmp_path):
    m = analyse_clip(_camera_clip(tmp_path / "steady.mp4", dx=+0.04))
    assert m.camera_steadiness == "steady", m.summary()
    assert m.camera_measurement_confidence == "adequate"


def test_decorrelated_water_is_not_called_shaky_on_weak_evidence(tmp_path):
    """A slow pan over mostly white water: the correlation is too weak to judge.

    This fixture (65% fresh noise, seed 6) was labelled "shaky" by the old
    scatter-only rule. Its windows correlate at ~0.03, far below the level at
    which a displacement can be measured, so steadiness is unverified.
    """
    m = analyse_clip(_camera_clip(tmp_path / "water.mp4", dx=+0.03, water=0.65, seed=6))
    assert m.camera_measurement_confidence == "weak", m.summary()
    assert m.camera_steadiness == "unverified", m.summary()
    assert any("confidence" in note for note in m.notes)
    assert not m.is_relaxation_suitable_movement, "unverified is never auto-approved"


def test_a_genuinely_uneven_move_over_structure_is_still_flagged(tmp_path):
    """The confidence rule must not hide real unsteadiness where it CAN be measured."""
    rng = np.random.default_rng(7)
    texture = _texture(rng, H * 4, W * 6)
    frames, x = [], W * 1.2
    for i in range(FPS * SECONDS):
        t = i / FPS
        x += (0.01 if t < 3 else 0.12 if t < 5 else 0.02) * W / FPS
        frames.append(_crop(texture, x, H * 2, 1.6))
    m = analyse_clip(_write(tmp_path / "uneven.mp4", frames))
    assert m.camera_measurement_confidence == "adequate"
    assert m.camera_steadiness in ("slightly_unsteady", "shaky"), m.summary()


def test_a_locked_off_shot_of_water_is_still_static(tmp_path):
    m = analyse_clip(_camera_clip(tmp_path / "locked.mp4", water=0.7, seed=5))
    assert m.camera_motion in ("static", "drift") and not m.is_moving_camera, m.summary()
