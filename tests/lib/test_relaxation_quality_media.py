"""Real-media tests for the three relaxation quality corrections.

These deliberately do not test for the presence of words in a document. Each
fixture is built with FFmpeg so its ground truth is known by construction, and
the assertions are about measured behaviour:

* **Camera motion vs subject motion.** A crop window travelling across a
  static texture is a camera move with no subject motion. The same texture
  held still with a moving element inside it is a static camera with subject
  motion. The second case is the one that matters: it is a fixed camera
  filming a river, and it must not be reported as camera movement.

* **Transition audit.** A hard-cut concatenation and an `xfade` dissolve
  between the same two clips must classify differently, or the audit cannot
  catch a render that substituted one for the other.

* **Stem balance.** Stems are generated at deliberately mismatched source
  levels, the balance is solved from their measured built loudness, the gains
  are actually applied, and the result is re-measured. The relationship is
  checked on the re-measured files — including the supporting group's combined
  level, which is the figure a per-layer check misses.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib import camera_motion
from lib.camera_motion import analyse_clip, movement_profile
from lib.stem_balance import (
    BalanceSpec,
    GroupSpec,
    StemBalanceError,
    distribute_group,
    measure_stem,
    power_sum_lufs,
    prominence_to_db,
    solve_balance,
)
from lib.transition_audit import (
    Transition,
    as_transitions,
    audit_rendered_boundaries,
    classify_boundary,
    frame_difference_profile,
    safe_chunk_boundaries,
    timeline_duration,
    transition_discipline,
    unexpected_cuts,
    validate_chunk_plan,
)


def _ffmpeg_or_skip() -> str:
    try:
        return camera_motion._binary("ffmpeg")
    except camera_motion.CameraMotionError:
        pytest.skip("ffmpeg not available for real-media fixtures")
    return ""


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"ffmpeg fixture build failed: {result.stderr[-400:]}")


# --------------------------------------------------------------------------
# Video fixtures with known ground truth
# --------------------------------------------------------------------------

_FPS = 30
_DURATION = 8
_OUT_W, _OUT_H = 640, 360


#: The solver-mechanics fixtures below exercise an EXAMPLE relationship (water
#: 6-8 dB under the music, the supporting group 12-16 dB under). These are test
#: data, not a pipeline default: the library has no default band any more, and
#: a channel states its own bands in its BRAND.md channel-mix block.
WATER_BAND_DB = (-8.0, -6.0)
SUPPORT_BAND_DB = (-16.0, -12.0)

@pytest.fixture(scope="module")
def ffmpeg() -> str:
    return _ffmpeg_or_skip()


@pytest.fixture(scope="module")
def media(ffmpeg: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build the clips. Ground truth is in how each is constructed."""
    out = tmp_path_factory.mktemp("relaxation_media")

    # A large, richly textured still. The texture matters: correlation needs
    # structure to lock onto, exactly as real rock and foliage provide it.
    plate = out / "plate.png"
    _run(
        [
            ffmpeg, "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=1280x720:rate=1",
            "-frames:v", "1", "-y", str(plate),
        ]
    )

    # CAMERA MOVE: the crop window travels across the still plate. Every pixel
    # of scene content translates together and nothing moves within the scene.
    # 35 px/s is a slow, relaxation-appropriate glide.
    camera_pan = out / "camera_pan.mp4"
    _run(
        [
            ffmpeg, "-v", "error", "-loop", "1", "-i", str(plate),
            "-t", str(_DURATION),
            "-vf",
            f"crop={_OUT_W}:{_OUT_H}:x='min(35*t\\,{1280 - _OUT_W})':y=0,"
            f"fps={_FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-y", str(camera_pan),
        ]
    )

    # STATIC CAMERA, MOVING SUBJECT: a fixed view, most of it filled with a
    # band of incoherent per-frame change, the rest static scenery. This is
    # the river case - a locked-off camera on churning water with banks and
    # rocks around it.
    #
    # The band must be INCOHERENT. An earlier attempt scrolled a rigid strip
    # of texture instead, and the module classified it as a leftward tracking
    # move - correctly, because a large region translating rigidly is what a
    # camera move looks like. Real froth decorrelates from frame to frame and
    # carries no coherent displacement, so per-frame random noise is the
    # faithful model and a sliding strip is not.
    static_subject = out / "static_subject.mp4"
    _run(
        [
            ffmpeg, "-v", "error",
            "-loop", "1", "-i", str(plate),
            "-f", "lavfi",
            "-i",
            f"nullsrc=size={_OUT_W}x230:rate={_FPS},"
            "geq=lum='random(1)*255':cb=128:cr=128",
            "-t", str(_DURATION),
            "-filter_complex",
            f"[0:v]crop={_OUT_W}:{_OUT_H}:x=200:y=200,fps={_FPS}[bg];"
            "[1:v]format=gray,format=yuv420p[froth];"
            "[bg][froth]overlay=x=0:y=130,format=yuv420p[v]",
            "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-y", str(static_subject),
        ]
    )

    # A fully locked-off shot, nothing moving at all.
    locked_off = out / "locked_off.mp4"
    _run(
        [
            ffmpeg, "-v", "error", "-loop", "1", "-i", str(plate),
            "-t", str(_DURATION),
            "-vf", f"crop={_OUT_W}:{_OUT_H}:x=300:y=150,fps={_FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-y", str(locked_off),
        ]
    )

    return {
        "plate": plate,
        "camera_pan": camera_pan,
        "static_subject": static_subject,
        "locked_off": locked_off,
    }


class TestCameraMotionIsNotSubjectMotion:
    """The correction the operator asked for, stated as measurements."""

    def test_travelling_crop_is_detected_as_camera_movement(
        self, media: dict[str, Path]
    ) -> None:
        m = analyse_clip(media["camera_pan"])
        assert m.is_moving_camera, (
            f"a travelling view was classified {m.camera_motion!r} - the module "
            "cannot see camera movement"
        )
        assert m.camera_motion in ("pan", "tilt", "tracking"), m.camera_motion
        assert m.camera_speed_band != "still"
        assert m.camera_consistency >= 0.6, (
            "a steady mechanical pan must register as a consistent direction"
        )

    def test_fixed_camera_on_a_moving_subject_is_NOT_camera_movement(
        self, media: dict[str, Path]
    ) -> None:
        """The whole point: moving water does not make a moving-camera shot."""
        m = analyse_clip(media["static_subject"])
        assert not m.is_moving_camera, (
            f"a locked-off camera with movement inside the frame was reported "
            f"as {m.camera_motion!r} camera movement - this is precisely the "
            "conflation that produced a pool of 47 static clips sold as a "
            "cinematic journey"
        )
        assert m.camera_motion in ("static", "drift")

    def test_the_two_signals_are_recorded_independently(
        self, media: dict[str, Path]
    ) -> None:
        moving_subject = analyse_clip(media["static_subject"])
        nothing_moving = analyse_clip(media["locked_off"])

        # Same camera behaviour...
        assert not moving_subject.is_moving_camera
        assert not nothing_moving.is_moving_camera

        # ...but subject motion tells them apart, which is only possible
        # because it is measured separately.
        assert moving_subject.subject_moving_fraction > (
            nothing_moving.subject_moving_fraction
        ), (
            "subject motion must distinguish a river from a still pond even "
            "when the camera is locked off in both"
        )
        assert nothing_moving.subject_motion == "still"

    def test_camera_movement_is_not_credited_to_the_subject(
        self, media: dict[str, Path]
    ) -> None:
        """A pan over a static scene has no subject motion of its own."""
        m = analyse_clip(media["camera_pan"])
        assert m.subject_moving_fraction < 0.6, (
            f"global camera displacement leaked into the subject figure "
            f"({m.subject_moving_fraction:.2f}); the two measurements are not "
            "independent"
        )

    def test_unanalysable_clip_raises_rather_than_defaulting_to_static(
        self, tmp_path: Path
    ) -> None:
        """An unscreened clip must never be recorded as a screened static one."""
        broken = tmp_path / "not_a_video.mp4"
        broken.write_bytes(b"not a video at all")
        with pytest.raises(camera_motion.CameraMotionError):
            analyse_clip(broken)

    def test_pool_profile_exposes_a_locked_off_pool(
        self, media: dict[str, Path]
    ) -> None:
        """The report that would have flagged the Test 2 pool up front."""
        measurements = [
            analyse_clip(media["camera_pan"]),
            analyse_clip(media["static_subject"]),
            analyse_clip(media["locked_off"]),
        ]
        profile = movement_profile(measurements)

        assert profile["clips"] == 3
        assert profile["moving_camera_clips"] == 1
        assert profile["moving_camera_share"] == pytest.approx(1 / 3, abs=0.01)
        assert profile["locked_off_moving_water_clips"] >= 1, (
            "a locked-off shot with in-frame motion must be counted as such"
        )
        assert "static" in profile["distinct_camera_motions"]


# --------------------------------------------------------------------------
# Transitions: rendered picture vs approved map
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def boundary_renders(
    ffmpeg: str, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Path]:
    """Two clips joined once by a hard cut and once by a real dissolve."""
    out = tmp_path_factory.mktemp("relaxation_boundaries")

    first, second = out / "a.mp4", out / "b.mp4"
    for path, extra in (
        (first, "format=yuv420p"),
        # Same generator, recoloured, so both clips carry equivalent texture
        # and the only thing that differs between the two renders below is
        # the JOIN. A static second clip would give a degenerate baseline.
        (second, "hue=h=150:s=1.4,negate,format=yuv420p"),
    ):
        _run(
            [
                ffmpeg, "-v", "error", "-f", "lavfi",
                "-i", "testsrc2=size=320x180:rate=30:duration=4",
                "-vf", extra, "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "18", "-y", str(path),
            ]
        )

    # Both joins are produced the same way - one filtergraph, one encode - so
    # the comparison isolates the transition type.
    #
    # The concat DEMUXER is deliberately not used here: on a stream-copy
    # concatenation this FFmpeg build reinitialises the filter chain at the
    # segment join, so `scdet` reports 0.0 for the first frame of the new
    # segment and the cut becomes invisible to any measurement. That is a
    # property of measuring a copied concat, not of the classifier, and it is
    # why Compose-stage auditing runs on the re-encoded delivered file.
    hard_cut = out / "hard_cut.mp4"
    _run(
        [
            ffmpeg, "-v", "error", "-i", str(first), "-i", str(second),
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-r", "30", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "18", "-y", str(hard_cut),
        ]
    )

    # A real 1.2 s dissolve between the same two clips.
    dissolve = out / "dissolve.mp4"
    _run(
        [
            ffmpeg, "-v", "error", "-i", str(first), "-i", str(second),
            "-filter_complex",
            "[0:v][1:v]xfade=transition=fade:duration=1.2:offset=2.8,"
            "format=yuv420p[v]",
            "-map", "[v]", "-r", "30", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "18", "-y", str(dissolve),
        ]
    )

    return {"hard_cut": hard_cut, "dissolve": dissolve}


class TestRenderedTransitionsMatchTheApprovedMap:
    def test_a_hard_cut_and_a_dissolve_classify_differently(
        self, boundary_renders: dict[str, Path]
    ) -> None:
        """Without this separation the audit cannot detect a substitution."""
        cut_kind, cut_peak, cut_base, cut_ratio = classify_boundary(
            frame_difference_profile(boundary_renders["hard_cut"], 4.0)
        )
        dis_kind, _, _, dis_ratio = classify_boundary(
            frame_difference_profile(boundary_renders["dissolve"], 3.4)
        )

        assert cut_kind == "cut", (
            f"a stream-copy concat join measured as {cut_kind!r} "
            f"(peak {cut_peak}, baseline {cut_base}, ratio {cut_ratio}x)"
        )
        assert dis_kind == "dissolve", (
            f"a real 1.2s xfade measured as {dis_kind!r} (ratio {dis_ratio}x)"
        )
        assert cut_ratio > dis_ratio

    def test_audit_flags_a_dissolve_that_rendered_as_a_cut(
        self, boundary_renders: dict[str, Path]
    ) -> None:
        """The exact Test 2 defect, reproduced and caught."""
        approved = [Transition(at_seconds=4.0, type="crossfade", duration_seconds=1.2)]

        observations = audit_rendered_boundaries(
            boundary_renders["hard_cut"], approved
        )
        assert len(observations) == 1
        assert not observations[0].matches
        assert unexpected_cuts(observations), (
            "an approved dissolve delivered as a hard cut was not reported"
        )

    def test_audit_passes_a_render_that_honoured_the_approved_dissolve(
        self, boundary_renders: dict[str, Path]
    ) -> None:
        approved = [Transition(at_seconds=3.4, type="crossfade", duration_seconds=1.2)]
        observations = audit_rendered_boundaries(
            boundary_renders["dissolve"], approved
        )
        assert observations[0].matches, observations[0].describe()
        assert not unexpected_cuts(observations)

    def test_chunk_assembly_does_not_introduce_unintended_cuts(
        self, boundary_renders: dict[str, Path]
    ) -> None:
        """Plan-level guard: the check that would have stopped the render.

        The Test 2 chunk plan put a boundary where a crossfade was approved,
        and the concat assembly turned it into a cut. Validating the plan
        catches that before a two-hour encode.
        """
        approved = as_transitions(
            [
                {"at_seconds": 0.0, "type": "cut", "duration_seconds": 0.0},
                {"at_seconds": 190.0, "type": "crossfade", "duration_seconds": 1.2},
                {"at_seconds": 400.0, "type": "cut", "duration_seconds": 0.0},
            ]
        )

        # A boundary on the approved dissolve is refused...
        violations = validate_chunk_plan(approved, [190.0])
        assert violations, "a chunk boundary on an approved dissolve was allowed"
        assert "silently replaced by a hard cut" in violations[0].describe()

        # ...and a boundary on a straight cut is fine.
        assert not validate_chunk_plan(approved, [400.0])

        # The proposed plan only ever lands on straight cuts.
        proposed = safe_chunk_boundaries(
            approved, target_chunk_seconds=200.0, total_seconds=600.0
        )
        assert proposed == [400.0]
        assert not validate_chunk_plan(approved, proposed)

    def test_overlap_arithmetic_matches_the_rendered_length(self) -> None:
        """The Test 2 numbers, which disagreed by 66 seconds."""
        holds = [16.61, 16.7, 19.66]
        transitions = as_transitions(
            [
                {"at_seconds": 16.61, "type": "crossfade", "duration_seconds": 1.2},
                {"at_seconds": 33.31, "type": "crossfade", "duration_seconds": 1.2},
            ]
        )
        assert timeline_duration(holds, transitions) == pytest.approx(
            sum(holds) - 2.4, abs=0.001
        )

        # A cut consumes nothing.
        cuts_only = as_transitions(
            [{"at_seconds": 16.61, "type": "cut", "duration_seconds": 0.0}]
        )
        assert timeline_duration(holds, cuts_only) == pytest.approx(
            sum(holds), abs=0.001
        )

    def test_a_uniform_dissolve_map_is_reported_as_a_pattern(self) -> None:
        """55 identical joins is not a set of decisions."""
        uniform = as_transitions(
            [{"at_seconds": 0.0, "type": "cut", "duration_seconds": 0.0}]
            + [
                {"at_seconds": 20.0 * i, "type": "crossfade", "duration_seconds": 1.2}
                for i in range(1, 30)
            ]
        )
        discipline = transition_discipline(uniform)
        assert discipline.dissolve_share == 1.0
        assert discipline.longest_identical_run == 29
        assert discipline.findings, "a fully uniform map produced no finding"
        assert any("default" in f for f in discipline.findings)

        # A mixed, deliberate map produces no complaint.
        mixed = as_transitions(
            [{"at_seconds": 0.0, "type": "cut", "duration_seconds": 0.0}]
            + [
                {
                    "at_seconds": 20.0 * i,
                    "type": "crossfade" if i % 4 == 0 else "cut",
                    "duration_seconds": (0.8 + 0.1 * (i % 5)) if i % 4 == 0 else 0.0,
                }
                for i in range(1, 30)
            ]
        )
        assert not transition_discipline(mixed).findings


# --------------------------------------------------------------------------
# Balance: real stems, measured, corrected, re-measured
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stems(ffmpeg: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Stems at deliberately mismatched source levels.

    The mismatch is the point. If every stem arrived at the same loudness, a
    literal application of the brief's percentages would happen to work, and
    the test would prove nothing.
    """
    out = tmp_path_factory.mktemp("relaxation_stems")
    recipes = {
        # A sparse tone, quiet at source - like the music in Test 2.
        "A1-music": "sine=frequency=330:duration=12,volume=-18dB",
        # Broadband noise, HOT at source - like the river stem that measured
        # -9.9 LUFS while the music sat at -16.0.
        "A2-water": "anoisesrc=color=pink:duration=12:amplitude=0.8",
        "A3-forest": "anoisesrc=color=brown:duration=12:amplitude=0.5",
        "A4-birds": "sine=frequency=2600:duration=12,volume=-26dB",
        "A5-wind": "anoisesrc=color=brown:duration=12:amplitude=0.2",
    }
    paths: dict[str, Path] = {}
    for role, recipe in recipes.items():
        path = out / f"{role}.wav"
        _run(
            [
                ffmpeg, "-v", "error", "-f", "lavfi", "-i", recipe,
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                "-y", str(path),
            ]
        )
        paths[role] = path
    return paths


def _apply_gain(ffmpeg: str, source: Path, gain_db: float, target: Path) -> Path:
    _run(
        [
            ffmpeg, "-v", "error", "-i", str(source),
            "-af", f"volume={gain_db:.2f}dB",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            "-y", str(target),
        ]
    )
    return target


@pytest.fixture(scope="module")
def spec() -> BalanceSpec:
    """The channel's relationship: music 100, water 40, ambience 20 combined."""
    return BalanceSpec(
        reference_role="A1-music",
        master_target_lufs=-16.0,
        roles={"A2-water": 0.40},
        groups={
            "ambience": GroupSpec(
                prominence=0.20,
                members={"A3-forest": 0.70, "A4-birds": 0.20, "A5-wind": 0.10},
            )
        },
    )


class TestBuiltStemGainsRespectTheRelativeBalance:
    def test_source_levels_really_are_mismatched(
        self, stems: dict[str, Path]
    ) -> None:
        """Guard the premise: a literal 0.4/0.2 would invert the hierarchy."""
        music = measure_stem(stems["A1-music"], short_term=False).integrated_lufs
        water = measure_stem(stems["A2-water"], short_term=False).integrated_lufs
        assert water > music, (
            "fixture premise broken: the water stem must arrive LOUDER than "
            "the music, as it did in the real project"
        )
        # Water at literal 40% amplitude would still not be 8 dB down.
        assert water + prominence_to_db(0.40) > music - 8.0

    def test_measured_gains_place_water_below_the_music(
        self, ffmpeg: str, stems: dict[str, Path], spec: BalanceSpec, tmp_path: Path
    ) -> None:
        """Solve, apply, re-measure. The relationship is checked on the files."""
        measured = {
            role: measure_stem(path, short_term=False).integrated_lufs
            for role, path in stems.items()
        }
        plan = solve_balance(
            spec,
            measured,
            water_role="A2-water",
            water_band=WATER_BAND_DB,
            group_bands={"ambience": SUPPORT_BAND_DB},
        )

        remeasured = {
            role: measure_stem(
                _apply_gain(
                    ffmpeg, stems[role], plan.gains_db[role], tmp_path / f"{role}.wav"
                ),
                short_term=False,
            ).integrated_lufs
            for role in stems
        }

        verification = plan.verify(remeasured, tolerance_lu=1.0)
        assert verification.passed, verification.failures

        water_offset = verification.achieved_offsets_db["A2-water"]
        low, high = min(WATER_BAND_DB), max(WATER_BAND_DB)
        assert low - 1.0 <= water_offset <= high + 1.0, (
            f"water landed {water_offset:+.2f} dB from the music, outside the "
            f"channel's {WATER_BAND_DB} band. Test 2 put it at 0.00 dB - a peer "
            "of the music - which is the defect being corrected"
        )
        assert water_offset < -3.0, "water is still effectively a peer of the music"

    def test_supporting_ambience_is_assessed_as_a_combined_group(
        self, ffmpeg: str, stems: dict[str, Path], spec: BalanceSpec, tmp_path: Path
    ) -> None:
        """The group's COMBINED level is what the brief constrains."""
        measured = {
            role: measure_stem(path, short_term=False).integrated_lufs
            for role, path in stems.items()
        }
        plan = solve_balance(
            spec,
            measured,
            water_role="A2-water",
            water_band=WATER_BAND_DB,
            group_bands={"ambience": SUPPORT_BAND_DB},
        )
        remeasured = {
            role: measure_stem(
                _apply_gain(
                    ffmpeg, stems[role], plan.gains_db[role], tmp_path / f"g_{role}.wav"
                ),
                short_term=False,
            ).integrated_lufs
            for role in stems
        }

        verification = plan.verify(remeasured, tolerance_lu=1.0)
        group_offset = verification.achieved_group_offsets_db["ambience"]

        low, high = min(SUPPORT_BAND_DB), max(SUPPORT_BAND_DB)
        assert low - 1.5 <= group_offset <= high + 1.5, (
            f"the combined forest+birds+wind group landed {group_offset:+.2f} dB "
            f"from the music, outside the channel's {SUPPORT_BAND_DB} band"
        )
        assert verification.in_band("ambience", SUPPORT_BAND_DB)

        # And it must be below the water, not merely below the music.
        assert group_offset < verification.achieved_offsets_db["A2-water"]

    def test_per_layer_allowance_would_overshoot_the_group(
        self, spec: BalanceSpec
    ) -> None:
        """Why grouping is not a formality.

        Giving each of three members the group's own allowance makes their
        combined output several dB louder than the brief asked for.
        """
        group = spec.groups["ambience"]
        allowance_db = prominence_to_db(group.prominence)

        # Wrong: each member reaches the allowance.
        naive = power_sum_lufs([allowance_db] * len(group.members))
        # Right: the members sum to the allowance.
        grouped = power_sum_lufs(
            list(distribute_group(allowance_db, group.members).values())
        )

        assert grouped == pytest.approx(allowance_db, abs=0.01)
        assert naive > allowance_db + 4.0, (
            "three members at the group allowance should sum well above it"
        )
        assert naive - grouped == pytest.approx(
            10 * math.log10(len(group.members)), abs=0.01
        )

    def test_gains_cannot_be_derived_from_a_missing_or_silent_stem(
        self, spec: BalanceSpec
    ) -> None:
        """An unmeasured stem is an error, not an assumption."""
        with pytest.raises(StemBalanceError, match="no measured built-stem"):
            solve_balance(spec, {"A1-music": -16.0})

        silent = {
            "A1-music": -16.0,
            "A2-water": -9.9,
            "A3-forest": -11.1,
            "A4-birds": -90.0,  # below the silence floor
            "A5-wind": -23.8,
        }
        with pytest.raises(StemBalanceError, match="silence floor"):
            solve_balance(spec, silent)

    def test_short_term_extremes_are_reported_for_transient_inspection(
        self, stems: dict[str, Path]
    ) -> None:
        """An integrated figure hides a spike; the short-term span shows it."""
        measurement = measure_stem(stems["A2-water"])
        assert measurement.short_term_max_lufs is not None
        assert measurement.short_term_min_lufs is not None
        assert measurement.short_term_span_lu is not None
        assert measurement.short_term_max_lufs >= measurement.short_term_min_lufs

    def test_prominence_reads_as_an_amplitude_ratio_inside_the_stated_bands(
        self,
    ) -> None:
        """The bridge from the operator's percentages to the dB bands."""
        assert prominence_to_db(1.0) == pytest.approx(0.0, abs=0.01)
        water = prominence_to_db(0.40)
        support = prominence_to_db(0.20)
        assert min(WATER_BAND_DB) <= water <= max(WATER_BAND_DB), water
        assert min(SUPPORT_BAND_DB) <= support <= max(SUPPORT_BAND_DB), support


# --------------------------------------------------------------------------
# Delivery: the approved mix is the only audio, and it survives the encode
# --------------------------------------------------------------------------


def _probe(ffmpeg: str, path: Path) -> dict:
    """Probe with the PATH-resolved ffprobe.

    Deliberately NOT derived from the ffmpeg path by string replacement: the
    resolved path contains "ffmpeg" in its parent directory too, so replacing
    the substring mangles it. Resolve ffprobe the same way production does.
    """
    del ffmpeg  # resolved independently, through PATH
    probe = subprocess.run(
        [camera_motion._binary("ffprobe"),
         "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    return json.loads(probe.stdout)


def _band_rms(
    ffmpeg: str,
    path: Path,
    filters: str = "",
    *,
    start: float | None = None,
    length: float | None = None,
) -> float:
    """Peak RMS level in dB, optionally over a window and a filtered band."""
    chain = (filters + "," if filters else "") + "astats=metadata=1:reset=0"
    args = [ffmpeg, "-nostats", "-hide_banner"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    args += ["-i", str(path), "-af", chain, "-f", "null", "-"]
    result = subprocess.run(args, capture_output=True, text=True)
    values = [
        float(m.group(1))
        for m in re.finditer(r"RMS level dB:\s*(-?\d+(?:\.\d+)?)", result.stderr)
    ]
    return max(values) if values else -120.0


@pytest.fixture(scope="module")
def delivery(ffmpeg: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """A picture that CARRIES ITS OWN AUDIO, plus a separate approved mix.

    Stock clips routinely ship with native sound, and the Asset Director may
    have classified it REJECT - voices, traffic, handling noise. The delivery
    must not contain it. The only way to prove that is to build a picture that
    has some and then check what reached the master.
    """
    out = tmp_path_factory.mktemp("relaxation_delivery")

    # Picture with a loud, unmistakable native tone at 4 kHz.
    picture = out / "picture_with_native_audio.mp4"
    _run([
        ffmpeg, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=4000:duration=8",
        "-vf", "format=yuv420p", "-r", "30",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-shortest", str(picture),
    ])

    # The approved mix: a low 120 Hz bed with a real fade-out at the end.
    approved = out / "approved_mix.wav"
    _run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=120:duration=8",
        "-af", "volume=-10dB,afade=t=in:st=0:d=1,afade=t=out:st=5:d=3",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(approved),
    ])
    return {"picture": picture, "approved": approved}


class TestDeliveredAudioContract:
    def test_the_picture_fixture_really_has_native_audio(
        self, ffmpeg: str, delivery: dict[str, Path]
    ) -> None:
        """Guard the premise: without native audio the next test proves nothing."""
        audio = [s for s in _probe(ffmpeg, delivery["picture"])["streams"]
                 if s["codec_type"] == "audio"]
        assert len(audio) == 1, "fixture picture should carry native audio"

    def test_rejected_native_audio_cannot_reach_the_master(
        self, ffmpeg: str, delivery: dict[str, Path], tmp_path: Path
    ) -> None:
        """`-map 0:v -map 1:a` is what keeps rejected sound out of delivery."""
        final = tmp_path / "final.mp4"
        _run([
            ffmpeg, "-v", "error", "-y",
            "-i", str(delivery["picture"]), "-i", str(delivery["approved"]),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
            "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
            "-shortest", str(final),
        ])
        audio = [s for s in _probe(ffmpeg, final)["streams"]
                 if s["codec_type"] == "audio"]
        assert len(audio) == 1, (
            f"delivered file has {len(audio)} audio streams; exactly one - the "
            "approved mix - may reach the master"
        )
        assert int(audio[0]["sample_rate"]) == 48000
        assert audio[0]["channels"] == 2

        # The native 4 kHz tone must be gone.
        full = _band_rms(ffmpeg, final)
        high = _band_rms(ffmpeg, final, "highpass=f=2000")
        assert high < full - 20, (
            f"delivered audio still carries high-band content ({high:.1f} dB "
            f"vs {full:.1f} dB full band) - the native tone leaked into the "
            "master"
        )

    def test_mapping_zero_a_would_leak_the_native_audio(
        self, ffmpeg: str, delivery: dict[str, Path], tmp_path: Path
    ) -> None:
        """The mistake the contract forbids, shown to be a real mistake."""
        leaked = tmp_path / "leaked.mp4"
        _run([
            ffmpeg, "-v", "error", "-y", "-i", str(delivery["picture"]),
            "-map", "0:v", "-map", "0:a", "-c:v", "copy",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(leaked),
        ])
        full = _band_rms(ffmpeg, leaked)
        high = _band_rms(ffmpeg, leaked, "highpass=f=2000")
        assert high > full - 15, (
            "the leak fixture should retain the native tone; if it does not, "
            "the previous test's assertion proves nothing"
        )

    def test_final_audio_duration_matches_the_picture(
        self, ffmpeg: str, delivery: dict[str, Path], tmp_path: Path
    ) -> None:
        """A mix longer than the picture hides the planned ending."""
        final = tmp_path / "dur.mp4"
        _run([
            ffmpeg, "-v", "error", "-y",
            "-i", str(delivery["picture"]), "-i", str(delivery["approved"]),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
            "-ar", "48000", "-ac", "2", "-shortest", str(final),
        ])
        data = _probe(ffmpeg, final)
        container = float(data["format"]["duration"])
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
        video_duration = float(video.get("duration") or container)
        audio_duration = float(audio.get("duration") or container)
        assert abs(video_duration - audio_duration) <= 0.15, (
            f"audio runs {audio_duration:.3f}s against a {video_duration:.3f}s "
            "picture - the planned ending is not where the viewer reaches it"
        )

    def test_true_peak_and_fade_survive_the_aac_encode(
        self, ffmpeg: str, delivery: dict[str, Path], tmp_path: Path
    ) -> None:
        """Measure the ENCODED file: a WAV inside the ceiling can exceed it."""
        ceiling_dbtp = -1.5
        aim_dbtp = -2.5

        limited = tmp_path / "limited.wav"
        _run([
            ffmpeg, "-v", "error", "-y", "-i", str(delivery["approved"]),
            "-af", f"alimiter=limit={10 ** (aim_dbtp / 20):.4f}:level=disabled",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(limited),
        ])

        final = tmp_path / "peak.mp4"
        _run([
            ffmpeg, "-v", "error", "-y",
            "-i", str(delivery["picture"]), "-i", str(limited),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
            "-shortest", str(final),
        ])

        encoded = measure_stem(final)
        assert encoded.true_peak_dbtp <= ceiling_dbtp, (
            f"true peak {encoded.true_peak_dbtp} dBTP exceeds the "
            f"{ceiling_dbtp} dBTP ceiling after the AAC encode - measuring the "
            "WAV alone would have missed this"
        )

        duration = float(_probe(ffmpeg, final)["format"]["duration"])
        tail = _band_rms(ffmpeg, final, start=duration - 0.4, length=0.35)
        mid = _band_rms(ffmpeg, final, start=duration / 2 - 0.5, length=1.0)
        assert tail < mid - 15, (
            f"tail {tail:.1f} dB against mid {mid:.1f} dB - the final fade did "
            "not survive into the delivered file"
        )
