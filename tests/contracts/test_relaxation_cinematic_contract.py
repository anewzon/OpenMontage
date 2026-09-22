"""Contract tests for the cinematic-quality corrections after the second test.

The operator's three findings on Test 2 were: predominantly stationary
ASMR-style water shots rather than a cinematic variety of moving-camera and
aerial footage; a soundtrack that was not music-led; and transitions that were
arbitrary and carried rendering defects.

The measurable behaviour lives in `tests/lib/test_relaxation_quality_media.py`,
which runs against real media. These tests hold the lines that are genuinely
documentary: that the channel's taste stays in its own `BRAND.md`, that the
generic relaxation Directors stay reusable by another channel, and that each
stage's review gate actually asks for the new checks — a gate that does not ask
is how prose guidance gets skipped, which is what happened in Test 2.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RELAX = ROOT / "skills" / "pipelines" / "relaxation"
MANIFEST = ROOT / "pipeline_defs" / "relaxation.yaml"
BRAND = ROOT.parent / "Channels" / "channel_0001" / "BRAND.md"
LIB = ROOT / "lib"

#: Words that name THIS channel's specific identity. A generic Director that
#: contains them has stopped being reusable.
CHANNEL_SPECIFIC = ("river flow naturescapes", "channel_0001")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _flat(text: str) -> str:
    """Collapse whitespace so a phrase split by markdown wrapping still matches.

    Without this, every prose assertion silently depends on where the source
    file happens to wrap - so reflowing a paragraph would fail a test that has
    nothing to do with the change. Blockquote markers are dropped first, since
    a wrapped line inside a `>` block would otherwise leave a stray marker in
    the middle of a sentence.
    """
    without_quotes = re.sub(r"(?m)^\s*>\s?", "", text)
    return re.sub(r"\s+", " ", without_quotes)


@pytest.fixture(scope="module")
def directors() -> dict[str, str]:
    return {
        path.stem: _flat(path.read_text(encoding="utf-8"))
        for path in RELAX.glob("*.md")
    }


def _brand_or_skip() -> str:
    if not BRAND.is_file():
        pytest.skip("channel_0001/BRAND.md not present (repo checked out standalone)")
    return _flat(BRAND.read_text(encoding="utf-8"))


def _stage(manifest: dict, name: str) -> dict:
    return next(s for s in manifest["stages"] if s["name"] == name)


def _focus(manifest: dict, name: str) -> str:
    stage = _stage(manifest, name)
    return " ".join(
        stage.get("review_focus", []) + stage.get("success_criteria", [])
    ).lower()


# --------------------------------------------------------------------------
# The channel's cinematic identity lives in BRAND.md
# --------------------------------------------------------------------------


class TestChannelOwnsItsVisualIdentity:
    def test_brand_asks_for_moving_camera_and_aerial_footage(self) -> None:
        low = _brand_or_skip().lower()
        for term in ("aerial", "drone", "tracking", "gliding", "pan"):
            assert term in low, f"BRAND.md no longer asks for {term} footage"
        assert "cinematic journey" in low, (
            "BRAND.md must state the channel is a cinematic journey, not a "
            "compilation of stationary shots"
        )

    def test_brand_forbids_stationary_shots_as_the_dominant_treatment(self) -> None:
        low = _brand_or_skip().lower()
        assert "dominant visual treatment" in low, (
            "BRAND.md must state that stationary water close-ups are "
            "occasional, not the dominant treatment"
        )
        assert "not** a compilation" in low or "not a compilation" in low

    def test_brand_distinguishes_camera_movement_from_subject_movement(self) -> None:
        """The distinction the operator asked to be made explicit."""
        brand = _brand_or_skip()
        low = brand.lower()
        assert "a fixed camera filming moving water is not a moving-camera shot" in low, (
            "BRAND.md must state the camera/subject distinction in terms that "
            "cannot be read as satisfied by moving water"
        )
        assert "separately" in low, (
            "BRAND.md must require the two to be recorded separately"
        )
        assert "camera_motion.py" in brand, (
            "BRAND.md must point at the measurement, so the distinction is not "
            "left to judgement alone"
        )

    def test_brand_rejects_title_based_acceptance(self) -> None:
        low = _brand_or_skip().lower()
        assert "title is not evidence" in low, (
            'BRAND.md must state that a clip titled "drone" is not evidence of '
            "camera movement"
        )

    def test_brand_requires_variety_without_fixing_a_ratio(self) -> None:
        """Variety is required; an identical per-episode recipe is not."""
        low = _brand_or_skip().lower()
        assert "do not hard-code an identical ratio" in low, (
            "BRAND.md must forbid a fixed drone/wide/close-up ratio per episode"
        )
        assert "sense of travel" in low, (
            "BRAND.md must require a coherent sense of travel instead of a ratio"
        )

    def test_brand_forbids_inventing_motion(self) -> None:
        low = _brand_or_skip().lower()
        assert "do not invent motion" in low
        for term in ("digital zoom", "slow motion"):
            assert term in low, f"BRAND.md must reject faked motion via {term}"

    def test_brand_requires_season_and_light_coherence(self) -> None:
        low = _brand_or_skip().lower()
        assert "season, light and environment must actually agree" in low
        assert "surface the creative shortfall" in low, (
            "BRAND.md must require a shortfall to be surfaced rather than "
            "concealed by forcing unsuitable assets into the timeline"
        )

    def test_brand_preserves_the_water_dominant_identity(self) -> None:
        """Aerial footage must not turn this into a mountain channel."""
        low = _brand_or_skip().lower()
        assert "water-dominant" in low
        assert "mountain montage" in low, (
            "BRAND.md must state that aerial footage reveals the river's world "
            "rather than replacing it"
        )

    def test_brand_states_restrained_transition_discipline(self) -> None:
        low = _brand_or_skip().lower()
        assert "straight cut" in low and "dissolve" in low
        assert "do not automatically apply a long" in low, (
            "BRAND.md must forbid applying a long dissolve to every boundary"
        )
        assert "fade through black" in low and "structural break" in low
        assert "technical render boundary must never decide" in low, (
            "BRAND.md must state that a chunk boundary does not choose the "
            "editorial transition"
        )
        assert "continuously across picture transitions" in low, (
            "BRAND.md must require continuous audio across picture changes"
        )


# --------------------------------------------------------------------------
# The generic Directors stay generic
# --------------------------------------------------------------------------


class TestDirectorsDoNotHardcodeThisChannel:
    def test_no_director_names_river_flow_as_its_own_identity(
        self, directors: dict[str, str]
    ) -> None:
        """Another relaxation channel must be able to use this pipeline."""
        for name, text in directors.items():
            low = text.lower()
            for token in CHANNEL_SPECIFIC:
                assert token not in low, (
                    f"{name}.md names {token!r} - channel identity belongs in "
                    "that channel's BRAND.md, not in a generic Director"
                )

    def test_directors_defer_to_the_calling_channel_for_movement_taste(
        self, directors: dict[str, str]
    ) -> None:
        """The procurement default was the upstream cause of the Test 2 pool."""
        procurement = directors["procurement-director"].lower()
        assert "brand.md" in procurement, (
            "procurement must read the calling channel's BRAND.md for the "
            "movement it wants"
        )
        assert "calm is not the same as motionless" in procurement, (
            "procurement previously preferred a static camera by default; it "
            "must now say so and correct it"
        )
        assert not re.search(
            r"\*\*calm camera movement\*\*\s*[—-]\s*static", procurement
        ), "procurement still defaults to preferring a static camera"

    def test_no_director_carries_a_per_role_loudness_table(
        self, directors: dict[str, str]
    ) -> None:
        """Balance taste is the channel's; the engineering is the pipeline's."""
        for name, text in directors.items():
            assert not re.search(r"−\s*\d+\s*LUFS\s*\|", text), (
                f"{name}.md carries a per-role target table - that is channel "
                "taste and belongs in BRAND.md"
            )

    def test_no_director_hardcodes_the_prominence_percentages(
        self, directors: dict[str, str]
    ) -> None:
        for name, text in directors.items():
            assert not re.search(r"100%.{0,40}40%.{0,40}20%", text, re.S), (
                f"{name}.md hard-codes this channel's prominence relationship"
            )


# --------------------------------------------------------------------------
# Asset analysis: camera motion is measured, and kept apart
# --------------------------------------------------------------------------


class TestAssetAnalysisMeasuresCameraMotion:
    def test_asset_director_requires_separate_measured_fields(
        self, directors: dict[str, str]
    ) -> None:
        text = directors["asset-director"]
        low = text.lower()
        assert "camera_motion" in low and "subject_motion" in low, (
            "the asset director must require both fields"
        )
        assert "a fixed camera filming moving water is not a moving-camera shot" in low
        assert "lib.camera_motion" in low or "camera_motion.py" in low, (
            "the asset director must name the measurement it uses"
        )

    def test_asset_director_explains_why_video_analyzer_is_not_enough(
        self, directors: dict[str, str]
    ) -> None:
        """cv2 is absent here, so that tool returns 'unknown' for every clip."""
        low = directors["asset-director"].lower()
        assert "cv2" in low, (
            "the asset director must record why video_analyzer's motion "
            "classifier cannot supply this on this installation"
        )

    def test_asset_director_forbids_defaulting_an_unanalysed_clip(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["asset-director"].lower()
        assert "never `static`" in low or "never static" in low, (
            "an unscreened clip must be recorded as unknown, not as static"
        )

    def test_asset_director_rejects_a_constant_trim(
        self, directors: dict[str, str]
    ) -> None:
        """Test 2 recorded 0.6s in and duration-0.6s out for all 56 clips."""
        low = directors["asset-director"].lower()
        assert "constant trim" in low, (
            "the asset director must reject a formulaic usable range"
        )

    def test_assets_stage_gate_requires_the_measurement(self, manifest: dict) -> None:
        focus = _focus(manifest, "assets")
        assert "camera" in focus and "separately" in focus, (
            "the assets gate must require camera motion recorded separately "
            "from subject motion"
        )
        assert "movement_profile" in focus, (
            "the assets gate must require the pool's movement profile"
        )
        assert "never as movement" in focus or "never defaulted" in focus

    def test_procurement_gate_requires_measured_screening(
        self, manifest: dict
    ) -> None:
        focus = _focus(manifest, "procurement")
        assert "title" in focus, (
            "the procurement gate must reject title-based acceptance"
        )
        assert "measured" in focus
        assert "movement_profile" in focus


# --------------------------------------------------------------------------
# Scene planning: coherence and progression, screened before the render
# --------------------------------------------------------------------------


class TestScenePlanningRejectsAnUnsupportedConcept:
    def test_scene_director_requires_progression_not_a_shot_collection(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["scene-director"].lower()
        assert "coherent progression" in low
        assert "sense of travel" in low
        assert "not a collection of good shots" in low

    def test_scene_director_must_refuse_an_unsupported_season_or_light_concept(
        self, directors: dict[str, str]
    ) -> None:
        """The concept is not realised by quietly mixing incompatible footage."""
        low = directors["scene-director"].lower()
        assert "season" in low and "light" in low
        assert "cannot deliver" in low or "cannot support" in low, (
            "the scene director must say what happens when the pool cannot "
            "deliver the concept"
        )
        assert "say so now" in low, (
            "the shortfall must be raised at planning time, not discovered in "
            "the render"
        )
        assert "do not force a prewritten movement structure" in low

    def test_scene_director_forbids_runs_of_static_close_ups(
        self, directors: dict[str, str]
    ) -> None:
        """Subject motion is not shot variety.

        Stated channel-neutrally: another relaxation channel's subject is rain
        or a fireplace, and the rule is the same for it.
        """
        low = directors["scene-director"].lower()
        assert "even when the subject" in low, (
            "subject motion must not be accepted as shot variety"
        )
        assert "subject motion is not shot variety" in low

    def test_scene_plan_gate_screens_before_the_render(self, manifest: dict) -> None:
        focus = _focus(manifest, "scene_plan")
        for requirement in (
            "camera motion",
            "sense of travel",
            "season",
            "progression",
        ):
            assert requirement in focus, (
                f"the scene_plan gate does not ask about {requirement}"
            )
        assert "in/out range" in focus, (
            "every slot's in/out range must be required to exist"
        )

    def test_scene_plan_findings_must_be_actionable(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["scene-director"].lower()
        assert "not actionable" in low, (
            "the scene director must require specific slots to be named rather "
            'than "add more variety"'
        )


# --------------------------------------------------------------------------
# Transitions: decided by the edit, preserved by the render
# --------------------------------------------------------------------------


class TestTransitionsSurviveIntoTheRender:
    def test_edit_director_forbids_the_dissolve_as_a_default(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["edit-director"].lower()
        assert "do not automatically apply a long" in low
        assert "55 of 56" in low, (
            "the edit director should record the actual Test 2 figure, so the "
            "rule is anchored to what went wrong"
        )
        assert "transition_discipline" in low

    def test_edit_director_requires_overlap_corrected_positions(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["edit-director"].lower()
        assert "overlap-corrected" in low
        assert "852.3" in low, (
            "the edit director should carry the real arithmetic that was got "
            "wrong, not an abstract warning"
        )
        assert "timeline_duration" in low

    def test_edit_director_requires_a_validated_chunk_plan(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["edit-director"].lower()
        assert "validate_chunk_plan" in low
        assert "list" in low
        assert "not permission to change the transition" in low, (
            "a chunk-plan violation must not be resolved by dropping the "
            "approved dissolve"
        )

    def test_compose_director_forbids_the_silent_substitution(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["compose-director"].lower()
        assert "cannot dissolve" in low
        assert "do not substitute a cut" in low
        assert "safe_chunk_boundaries" in low
        assert "20.2" in low, (
            "the compose director should carry the measured evidence of the "
            "defect it is preventing"
        )

    def test_compose_director_audits_rendered_boundaries(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["compose-director"].lower()
        assert "audit_rendered_boundaries" in low
        assert "unexpected_cuts" in low
        assert "boundary frames" in low, (
            "measuring a boundary is not the same as looking at it"
        )

    def test_edit_gate_requires_transition_discipline(self, manifest: dict) -> None:
        focus = _focus(manifest, "edit")
        assert "dissolve is not the default" in focus
        assert "transition_discipline" in focus
        assert "overlap" in focus
        assert "no chunk boundary falls on or beside an approved dissolve" in focus

    def test_compose_gate_requires_the_boundary_audit(self, manifest: dict) -> None:
        focus = _focus(manifest, "compose")
        assert "approved transition map" in focus
        assert "unexpected" in focus and "hard cut" in focus
        assert "unexpected_cuts is empty" in focus, (
            "the compose stage must not pass with an unaudited boundary"
        )


# --------------------------------------------------------------------------
# The mix is music-led, and the metadata describes what ran
# --------------------------------------------------------------------------


class TestMusicLedMixIsRequiredByTheGates:
    def test_edit_gate_requires_a_music_led_mix(self, manifest: dict) -> None:
        focus = _focus(manifest, "edit")
        assert "music-led" in focus
        assert "not mixed as its peer" in focus, (
            "the edit gate must reject water mixed at parity with the music - "
            "the exact Test 2 defect"
        )
        assert "combined group" in focus

    def test_edit_gate_requires_built_stem_measurement(self, manifest: dict) -> None:
        focus = _focus(manifest, "edit")
        assert "built stems" in focus
        assert "never from source means" in focus, (
            "the improved Test 2 method must not be allowed to regress"
        )

    def test_edit_director_keeps_the_built_stem_method(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["edit-director"].lower()
        assert "measure the built stem" in low
        assert "-9.9" in low and "-16.3" in low, (
            "the edit director should carry the measured evidence for why the "
            "source mean is the wrong basis"
        )
        assert "solve_balance" in low

    def test_edit_director_requires_group_solving(
        self, directors: dict[str, str]
    ) -> None:
        low = directors["edit-director"].lower()
        assert "group the supporting layers" in low
        assert "sum" in low

    def test_metadata_must_describe_the_executed_mix(self, manifest: dict) -> None:
        focus = _focus(manifest, "edit")
        assert "executed mix" in focus
        assert "recorded mix duration equals" in focus, (
            "Test 2 recorded a 926.3s timeline for an 864.03s mix"
        )

    def test_compose_gate_still_checks_format_and_audio(self, manifest: dict) -> None:
        """The corrections must not have loosened the existing QC."""
        focus = _focus(manifest, "compose")
        for requirement in (
            "3840x2160",
            "yuv420p",
            "aac",
            "48 khz",
            "true peak",
            "exactly one audio stream",
        ):
            assert requirement in focus, (
                f"the compose gate no longer requires {requirement}"
            )

    def test_honesty_rules_survive(self, manifest: dict, directors: dict[str, str]) -> None:
        focus = _focus(manifest, "compose")
        assert "declared, never recorded as passes" in focus
        assert "subjective listening never reported as a pass" in focus, (
            "an unperformed listening review must not be reported as a pass"
        )
        assert "never record a pass for a check that did not run" in (
            directors["compose-director"].lower()
        )


# --------------------------------------------------------------------------
# The helper modules exist and stay channel-neutral
# --------------------------------------------------------------------------


class TestHelperModulesAreGenericInfrastructure:
    @pytest.mark.parametrize(
        "module", ("camera_motion.py", "stem_balance.py", "transition_audit.py")
    )
    def test_module_is_present(self, module: str) -> None:
        assert (LIB / module).is_file(), f"lib/{module} is missing"

    @pytest.mark.parametrize(
        "module", ("camera_motion.py", "stem_balance.py", "transition_audit.py")
    )
    def test_module_holds_no_channel_identity(self, module: str) -> None:
        low = (LIB / module).read_text(encoding="utf-8").lower()
        assert "river flow naturescapes" not in low, (
            f"lib/{module} names a specific channel"
        )

    def test_stem_balance_holds_no_channel_percentages_as_defaults(self) -> None:
        """The relationship is the channel's; the module only solves it."""
        from lib.stem_balance import BalanceSpec

        spec = BalanceSpec(reference_role="A1", master_target_lufs=-16.0)
        assert spec.roles == {}
        assert spec.groups == {}

    def test_transition_audit_states_its_detection_method(self) -> None:
        from lib.transition_audit import audit_report

        report = audit_report([])
        method = report["method"].lower()
        assert "scdet" in method
        assert "encoded" in method, (
            "the audit must say it measures the encoded file"
        )
