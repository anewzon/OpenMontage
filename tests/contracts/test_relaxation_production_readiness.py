"""Production-readiness contracts for the relaxation pipeline.

These are the checks that must hold before VidQwik starts real production.
They cover the manifest, the stage graph, tool and skill registration, the
artifact-schema contracts, the duration policy, the channel-opening contract
and the FFmpeg runtime.

Where a claim is behavioural rather than declarative it is tested against real
media in `tests/lib/test_relaxation_quality_media.py`; this module holds the
declarative half and the schema validation.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import jsonschema
import pytest
import yaml
from tests._paths import channel_brand

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "pipeline_defs" / "relaxation.yaml"
MANIFEST_SCHEMA = ROOT / "schemas" / "pipelines" / "pipeline_manifest.schema.json"
ARTIFACT_SCHEMAS = ROOT / "schemas" / "artifacts"
SKILLS = ROOT / "skills"
RELAX = SKILLS / "pipelines" / "relaxation"
LIB = ROOT / "lib"
BRAND = channel_brand("channel_0001")

#: The stage graph the pipeline promises. Order is load-bearing: media is
#: analysed before the edit is cast from it, and procurement gates before
#: anything is analysed.
EXPECTED_STAGE_ORDER = [
    "research", "proposal", "procurement", "assets",
    "scene_plan", "edit", "compose", "publish",
]

#: The project-local FFmpeg distribution that was removed. No active code or
#: config may reference it again.
REMOVED_FFMPEG_MARKERS = ("ffmpeg-7.1.1-full_build", "OPENMONTAGE_FFMPEG_DIR")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def directors() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in RELAX.glob("*.md")}


def _schema(name: str) -> dict:
    return json.loads((ARTIFACT_SCHEMAS / f"{name}.schema.json").read_text("utf-8"))


def _concept_options(count: int = 3) -> list[dict]:
    """Minimum-viable concept options for proposal_packet validation."""
    return [
        {
            "id": f"c{i}", "title": f"Concept {i}",
            "hook": "a quiet morning on the upper rapids",
            "narrative_structure": "journey",
            "visual_approach": "aerial reveal into immersive river, detail rest",
            "target_duration_seconds": 900,
            "why_this_works": "grounded in the research brief's observed gap",
        }
        for i in range(1, count + 1)
    ]


def _stage(manifest: dict, name: str) -> dict:
    return next(s for s in manifest["stages"] if s["name"] == name)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s*>\s?", "", text))


# ==========================================================================
# 1-3. Manifest, stage order, human gates
# ==========================================================================


class TestManifest:
    def test_manifest_is_schema_valid(self, manifest: dict) -> None:
        jsonschema.validate(manifest, json.loads(MANIFEST_SCHEMA.read_text("utf-8")))

    def test_stage_order_is_preserved(self, manifest: dict) -> None:
        assert [s["name"] for s in manifest["stages"]] == EXPECTED_STAGE_ORDER

    def test_human_gates_are_declared_and_enforceable(self, manifest: dict) -> None:
        """A gate is only real if the stage also checkpoints."""
        gated = [s for s in manifest["stages"] if s.get("human_approval_default")]
        assert gated, "the pipeline declares no human approval gates"

        names = {s["name"] for s in gated}
        for required in ("proposal", "procurement"):
            assert required in names, f"{required} must be a human gate"

        for stage in gated:
            assert stage.get("checkpoint_required") is True, (
                f"{stage['name']} requires human approval but does not "
                "checkpoint - nothing would hold the run"
            )

    def test_description_describes_the_implemented_system(self, manifest: dict) -> None:
        low = _flat(manifest["description"]).lower()
        assert "60 seconds" in low and "5 hours" in low
        for mode in ("licensed_manual", "free_auto"):
            assert mode in low, f"description omits the {mode} sourcing mode"
        assert "brand.md" in low
        assert "60-180" not in low and "60–180" not in low, (
            "description still claims a 60-180 minute-only pipeline"
        )


# ==========================================================================
# 4-5. Skill registration
# ==========================================================================


class TestSkillRegistration:
    def test_every_stage_skill_path_exists(self, manifest: dict) -> None:
        for stage in manifest["stages"]:
            path = SKILLS / f"{stage['skill']}.md"
            assert path.is_file(), f"{stage['name']}: missing {path}"

    def test_every_required_skill_exists(self, manifest: dict) -> None:
        for skill in manifest["required_skills"]:
            assert (SKILLS / f"{skill}.md").is_file(), f"missing skill: {skill}"

    def test_every_stage_skill_is_declared_required(self, manifest: dict) -> None:
        """compose-director was previously missing from required_skills."""
        required = set(manifest["required_skills"])
        for stage in manifest["stages"]:
            assert stage["skill"] in required, (
                f"{stage['skill']} runs a stage but is not in required_skills"
            )

    def test_relaxation_directors_are_indexed(self) -> None:
        index = (SKILLS / "INDEX.md").read_text(encoding="utf-8")
        assert "pipelines/relaxation/" in index, (
            "skills/INDEX.md does not index the relaxation pipeline"
        )
        for director in (
            "executive-producer", "research-director", "proposal-director",
            "procurement-director", "asset-director", "scene-director",
            "edit-director", "compose-director", "publish-director",
        ):
            assert f"pipelines/relaxation/{director}.md" in index, (
                f"skills/INDEX.md omits {director}"
            )


# ==========================================================================
# 6-7. Tool registration
# ==========================================================================


class TestToolRegistration:
    def _declared(self, manifest: dict) -> set[str]:
        names: set[str] = set()
        for stage in manifest["stages"]:
            for key in ("tools_available", "required_tools", "optional_tools"):
                names.update(stage.get(key) or [])
        return names

    def test_every_declared_tool_resolves_in_the_registry(self, manifest: dict) -> None:
        """A manifest naming a tool the registry does not have is a broken wire."""
        from tools.tool_registry import ToolRegistry

        registry = ToolRegistry()
        try:
            registry.discover()
        except Exception:  # noqa: BLE001 - discovery is best-effort
            pass

        missing = [n for n in sorted(self._declared(manifest)) if registry.get(n) is None]
        assert not missing, f"declared but not in the tool registry: {missing}"

    def test_required_and_optional_tools_are_subsets_of_available(
        self, manifest: dict
    ) -> None:
        for stage in manifest["stages"]:
            available = set(stage.get("tools_available") or [])
            for key in ("required_tools", "optional_tools"):
                extra = set(stage.get(key) or []) - available
                assert not extra, (
                    f"{stage['name']}.{key} declares {sorted(extra)} which is "
                    "not in tools_available"
                )

    def test_procurement_exposes_the_free_auto_provider_tools(
        self, manifest: dict
    ) -> None:
        """The Director documented native acquisition; the manifest must wire it."""
        available = set(_stage(manifest, "procurement").get("tools_available") or [])
        for tool in ("direct_clip_search", "pexels_video", "pixabay_video",
                     "pixabay_music"):
            assert tool in available, f"procurement cannot reach {tool}"

    def test_native_analysis_tools_are_wired(self, manifest: dict) -> None:
        assert "frame_sampler" in (_stage(manifest, "assets")["tools_available"])
        compose = set(_stage(manifest, "compose")["tools_available"])
        assert {"frame_sampler", "visual_qa"} <= compose
        assert "frame_sampler" in (_stage(manifest, "publish")["tools_available"])


# ==========================================================================
# 8-9. Sourcing modes
# ==========================================================================


class TestSourcingModes:
    def test_both_sourcing_modes_remain_valid(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        procurement = _flat(directors["procurement-director"])
        for mode in ("licensed_manual", "free_auto"):
            assert mode in procurement, f"procurement no longer documents {mode}"
        focus = " ".join(_stage(manifest, "procurement")["review_focus"])
        for mode in ("licensed_manual", "free_auto"):
            assert mode in focus, f"the procurement gate no longer checks {mode}"

    def test_human_gate_survives_in_both_modes(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        procurement = _flat(directors["procurement-director"]).lower()
        assert "human gate remains" in procurement or "gate remains" in procurement
        assert _stage(manifest, "procurement")["human_approval_default"] is True

    def test_licensed_manual_never_auto_downloads_paid_stock(
        self, manifest: dict
    ) -> None:
        focus = " ".join(_stage(manifest, "procurement")["review_focus"]).lower()
        assert "no automated paid-stock downloading" in focus

    def test_sourcing_mode_comes_from_the_proposal_not_a_channel_rule(
        self, manifest: dict
    ) -> None:
        focus = " ".join(_stage(manifest, "procurement")["review_focus"])
        assert "proposal_packet.metadata.sourcing" in focus
        assert "not assumed" in focus


# ==========================================================================
# 10-15. Duration and chunking policy
# ==========================================================================


class TestDurationPolicy:
    def test_minimum_duration_is_sixty_seconds(self) -> None:
        from lib.relaxation_policy import duration_bounds

        assert duration_bounds()[0] == 60

    def test_maximum_duration_is_five_hours(self) -> None:
        from lib.relaxation_policy import duration_bounds

        assert duration_bounds()[1] == 18000

    @pytest.mark.parametrize("seconds", [60, 90, 900, 3600, 10800, 18000])
    def test_supported_durations_are_accepted(self, seconds: int) -> None:
        from lib.relaxation_policy import validate_duration

        assert validate_duration(seconds) == float(seconds)

    @pytest.mark.parametrize("seconds", [59, 0, -1, 18001, 36000])
    def test_unsupported_durations_are_rejected(self, seconds: int) -> None:
        """Rejected, never clamped. A silently shortened film is not the ask."""
        from lib.relaxation_policy import DurationOutOfRange, validate_duration

        with pytest.raises(DurationOutOfRange):
            validate_duration(seconds)

    def test_rejection_message_tells_the_operator_not_to_clamp(self) -> None:
        from lib.relaxation_policy import DurationOutOfRange, validate_duration

        with pytest.raises(DurationOutOfRange, match="do not clamp"):
            validate_duration(59)

    @pytest.mark.parametrize("seconds", [60, 120, 600, 1200])
    def test_short_productions_do_not_require_chunking(self, seconds: int) -> None:
        from lib.relaxation_policy import chunking_required

        assert chunking_required(seconds) is False

    @pytest.mark.parametrize("seconds", [1201, 1800, 7200, 18000])
    def test_long_productions_require_a_chunk_plan(self, seconds: int) -> None:
        from lib.relaxation_policy import chunking_required

        assert chunking_required(seconds) is True

    def test_twenty_minute_threshold_matches_the_manifest(self, manifest: dict) -> None:
        from lib.relaxation_policy import chunk_threshold_seconds

        assert chunk_threshold_seconds() == 1200
        assert manifest["metadata"]["chunking"]["threshold_seconds"] == 1200

    def test_policy_fails_loudly_if_the_manifest_stops_declaring_it(self) -> None:
        """A policy with no source of truth must error, not fall back."""
        from lib.relaxation_policy import duration_bounds

        with pytest.raises(RuntimeError, match="no source of truth"):
            duration_bounds({"metadata": {}})

    def test_proposal_stage_validates_duration(self, manifest: dict) -> None:
        stage = _stage(manifest, "proposal")
        joined = " ".join(stage["review_focus"] + stage["success_criteria"])
        assert "18000" in joined and "60" in joined
        assert "not silently clamped" in joined or "surfaced" in joined

    def test_wall_time_budget_admits_a_five_hour_production(
        self, manifest: dict
    ) -> None:
        """600 minutes could read as a reason to refuse a 5-hour film."""
        minutes = manifest["orchestration"]["max_wall_time_minutes"]
        assert minutes >= 18000 / 60, (
            f"max_wall_time_minutes={minutes} is below the maximum delivered "
            "runtime alone, before analysis, mixing, encoding and QC"
        )

    def test_directors_do_not_hardcode_a_sixty_to_one_eighty_minute_pipeline(
        self, directors: dict[str, str]
    ) -> None:
        for name, text in directors.items():
            flat = _flat(text)
            assert "60–180 minute" not in flat and "60-180 minute" not in flat, (
                f"{name}.md still describes a 60-180 minute-only pipeline"
            )

    def test_scene_director_does_not_impose_a_fixed_hold_band(
        self, directors: dict[str, str]
    ) -> None:
        """15-55s is a long-form observation, not a rule for a 60s piece."""
        flat = _flat(directors["scene-director"])
        assert "not a rule and not a floor" in flat, (
            "the scene director must state that the 15-55s band is not a rule"
        )
        assert "total approved runtime" in flat


# ==========================================================================
# 17-19. Artifact-schema contracts
# ==========================================================================


class TestArtifactSchemaContracts:
    def test_render_report_schema_forbids_a_top_level_qc_key(self) -> None:
        """The premise of the metadata.qc rule."""
        schema = _schema("render_report")
        assert schema["additionalProperties"] is False
        assert "qc" not in schema["properties"]

    def test_render_report_qc_validates_under_metadata(self) -> None:
        schema = _schema("render_report")
        report = {
            "version": "1.0",
            "outputs": [{
                "path": "output/final.mp4", "format": "mp4",
                "resolution": "3840x2160", "duration_seconds": 3600.0,
                "codec": "h264", "audio_codec": "aac", "fps": 30,
            }],
            "verification_notes": ["measured on the encoded file"],
            "metadata": {"qc": {
                "technical": {"resolution": "3840x2160", "fps": 30,
                              "pix_fmt": "yuv420p", "audio_stream_count": 1,
                              "duration_seconds": 3600.0,
                              "clean_full_file_decode": True},
                "audio": {"integrated_lufs": -16.0, "true_peak_dbtp": -2.0,
                          "final_fade_present": True},
                "transitions": {"boundaries_inspected": 42,
                                "unexpected_hard_cuts": [], "passed": True},
                "editorial": {"moving_camera_present": True},
                "brand": {"opening_present": True},
            }},
        }
        jsonschema.validate(report, schema)

    def test_render_report_with_top_level_qc_is_rejected(self) -> None:
        """Proves the old Director instruction would actually have failed."""
        schema = _schema("render_report")
        report = {
            "version": "1.0",
            "outputs": [{"path": "o.mp4", "format": "mp4",
                         "resolution": "1920x1080", "duration_seconds": 10.0}],
            "qc": {"resolution": "1920x1080"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(report, schema)

    def test_scene_plan_rejects_relaxation_fields_on_scene_entries(self) -> None:
        schema = _schema("scene_plan")
        plan = {
            "version": "1.0",
            "scenes": [{
                "id": "M1_001", "type": "broll", "description": "river",
                "start_seconds": 0.0, "end_seconds": 18.0,
                "asset_id": "pexels_4318716",     # not a canonical field
                "camera_motion": "tracking",       # not a canonical field
            }],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(plan, schema)

    def test_scene_plan_relaxation_slots_validate_under_metadata(self) -> None:
        """A realistic relaxation scene plan, schema-valid."""
        schema = _schema("scene_plan")
        plan = {
            "version": "1.0",
            "scenes": [
                {"id": "M1_001", "type": "broll",
                 "description": "Aerial track over rapids",
                 "start_seconds": 0.0, "end_seconds": 18.0,
                 "shot_intent": "establish the journey with real movement",
                 "transition_in": "cut", "transition_out": "cut",
                 "required_assets": [{"type": "video",
                                      "description": "pexels_4318716",
                                      "source": "provided"}]},
                {"id": "M1_002", "type": "broll",
                 "description": "Immersive mossy stream",
                 "start_seconds": 18.0, "end_seconds": 42.0,
                 "shot_intent": "settle and dwell",
                 "transition_in": "cut", "transition_out": "crossfade",
                 "required_assets": [{"type": "video",
                                      "description": "pexels_11404058",
                                      "source": "provided"}]},
            ],
            "metadata": {
                "relaxation_slots": [
                    {"scene_id": "M1_001", "asset_id": "pexels_4318716",
                     "usable_in_seconds": 0.6, "usable_out_seconds": 18.6,
                     "shot_scale": "wide", "camera_motion": "tracking",
                     "camera_direction": "down", "subject_motion": "strong",
                     "season": "indeterminate", "weather": "overcast",
                     "light": "diffuse overcast", "target_hold_seconds": 18.0,
                     "editorial_reason": "leads with measured camera movement"},
                    {"scene_id": "M1_002", "asset_id": "pexels_11404058",
                     "usable_in_seconds": 6.0, "usable_out_seconds": 30.0,
                     "shot_scale": "medium", "camera_motion": "static",
                     "camera_direction": None, "subject_motion": "strong",
                     "season": "autumn", "weather": "overcast",
                     "light": "diffuse overcast", "target_hold_seconds": 24.0,
                     "editorial_reason": "the immersive river, used once"},
                ],
                "target_duration_seconds": 42.0,
            },
        }
        jsonschema.validate(plan, schema)

        slot_ids = {s["scene_id"] for s in plan["metadata"]["relaxation_slots"]}
        scene_ids = {s["id"] for s in plan["scenes"]}
        assert slot_ids == scene_ids, "every slot must map to a scene and back"

    def test_asset_manifest_rejects_analysis_fields_on_asset_entries(self) -> None:
        schema = _schema("asset_manifest")
        bad = {
            "version": "1.0",
            "assets": [{"id": "a", "type": "video", "path": "p.mp4",
                        "source_tool": "pexels_video", "scene_id": "M1",
                        "camera_motion": "tracking"}],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)

    def test_asset_manifest_uses_duration_seconds_not_duration(self) -> None:
        schema = _schema("asset_manifest")
        item = schema["properties"]["assets"]["items"]
        assert "duration_seconds" in item["properties"]
        assert "duration" not in item["properties"]

        bad = {"version": "1.0",
               "assets": [{"id": "a", "type": "video", "path": "p.mp4",
                           "source_tool": "pexels_video", "scene_id": "M1",
                           "duration": 12.0}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)

    def test_asset_manifest_relaxation_analysis_validates_under_metadata(self) -> None:
        schema = _schema("asset_manifest")
        item = {
            "version": "1.0",
            "assets": [{
                "id": "pexels_4318716", "type": "video",
                "path": "assets/video/pexels_4318716.mp4",
                "source_tool": "pexels_video", "scene_id": "M1",
                "duration_seconds": 19.18, "resolution": "1920x1080",
                "format": "mp4", "provider": "pexels",
                "license": "Pexels License", "cost_usd": 0.0,
            }],
            "total_cost_usd": 0.0,
            "metadata": {
                "asset_analysis": {
                    "pexels_4318716": {
                        "probe": {"width": 1920, "height": 1080,
                                  "fps": "24/1", "pix_fmt": "yuv420p",
                                  "has_audio": True},
                        "usable_ranges": [{
                            "in_seconds": 0.6, "out_seconds": 18.6,
                            "shot_scale": "wide", "camera_motion": "tracking",
                            "camera_direction": "down",
                            "camera_speed_band": "graceful",
                            "camera_steadiness": "steady",
                            "camera_displacement_per_second": 0.067,
                            "subject_motion": "strong",
                            "subject_moving_fraction": 0.71,
                            "season": "indeterminate", "weather": "overcast",
                            "light": "diffuse overcast",
                            "planned_role": "hero",
                        }],
                        "loop_suspected": False,
                        "native_audio": {"classification": "USE",
                                         "measured_lufs": -21.4},
                        "flags": [],
                    }
                },
                "inventory_stats": {
                    "reuse_factor": 0.62,
                    "movement_profile": {"clips": 56, "moving_camera_clips": 7,
                                         "moving_camera_share": 0.125},
                },
            },
        }
        jsonschema.validate(item, schema)

    def test_directors_point_at_the_metadata_locations(
        self, directors: dict[str, str]
    ) -> None:
        assert "metadata.qc" in _flat(directors["compose-director"])
        assert "metadata.relaxation_slots" in _flat(directors["scene-director"])
        assert "metadata.asset_analysis" in _flat(directors["asset-director"])
        assert "duration_seconds" in _flat(directors["asset-director"])

    def test_no_director_still_writes_render_report_dot_qc(
        self, directors: dict[str, str]
    ) -> None:
        compose = directors["compose-director"]
        assert 'report["qc"]' not in compose, (
            "compose-director still shows writing a top-level qc key"
        )

    def test_proposal_opening_validates_under_metadata(self) -> None:
        """production_plan is closed; the opening contract lives in metadata."""
        schema = _schema("proposal_packet")
        packet = {
            "version": "1.0",
            "concept_options": _concept_options(),
            "selected_concept": {"concept_id": "c1", "rationale": "y"},
            "production_plan": {"pipeline": "relaxation", "stages": [],
                                "render_runtime": "ffmpeg"},
            "cost_estimate": {"total_estimated_usd": 0.0, "line_items": [],
                              "budget_verdict": "within_budget"},
            "approval": {"status": "approved"},
            "metadata": {
                "target_duration_seconds": 900,
                "sourcing": "free_auto",
                "opening": {
                    "required": True, "intended_duration_seconds": 8,
                    "composition": "RiverFlowOpening", "runtime": "remotion",
                    "roles": {"brand_signature": "small constant signature",
                              "welcome_message": "dominant original line",
                              "episode_line": "smallest, per episode"},
                    "bed": "moving water from this episode",
                },
            },
        }
        jsonschema.validate(packet, schema)

    def test_proposal_opening_on_production_plan_is_rejected(self) -> None:
        schema = _schema("proposal_packet")
        packet = {
            "version": "1.0",
            "concept_options": _concept_options(),
            "selected_concept": {"concept_id": "c1", "rationale": "y"},
            "production_plan": {"pipeline": "relaxation", "stages": [],
                                "render_runtime": "ffmpeg",
                                "opening": {"required": True}},
            "cost_estimate": {"total_estimated_usd": 0.0, "line_items": [],
                              "budget_verdict": "within_budget"},
            "approval": {"status": "approved"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(packet, schema)


# ==========================================================================
# 20-21. Channel-required opening
# ==========================================================================


class TestChannelOpeningIsBinding:
    def test_proposal_records_the_opening_contract(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        proposal = _flat(directors["proposal-director"])
        assert "metadata.opening" in proposal
        assert "BRAND.md" in proposal
        focus = " ".join(_stage(manifest, "proposal")["review_focus"]
                         + _stage(manifest, "proposal")["success_criteria"])
        assert "metadata.opening" in focus

    def test_edit_carries_the_opening_into_the_timeline(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        edit = _flat(directors["edit-director"])
        assert "edit_decisions.metadata.opening" in edit
        assert "timeline duration includes the opening" in edit.lower()
        assert "audio plan covers the opening" in edit.lower()
        focus = " ".join(_stage(manifest, "edit")["review_focus"])
        assert "opening" in focus.lower()

    def test_compose_renders_inspects_and_fails_on_a_missing_opening(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        compose = _flat(directors["compose-director"]).lower()
        assert "never silently omitted" in compose
        assert "missing required opening is a stage failure" in compose

        stage = _stage(manifest, "compose")
        joined = " ".join(stage["review_focus"] + stage["success_criteria"]).lower()
        assert "opening" in joined
        assert "never silently omitted" in joined or "silently omitted" in joined
        assert "brand-required opening is present" in joined

    def test_a_short_remotion_opening_is_not_a_runtime_swap(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        compose = _flat(directors["compose-director"]).lower()
        assert "is not** a silent runtime swap" in compose or \
               "is not a silent runtime swap" in compose
        focus = " ".join(_stage(manifest, "compose")["review_focus"]).lower()
        assert "not a full-body runtime swap" in focus

    def test_opening_requirement_is_channel_owned_not_pipeline_owned(
        self, directors: dict[str, str]
    ) -> None:
        """Another relaxation channel may want no opening at all."""
        for name, text in directors.items():
            flat = _flat(text).lower()
            assert "river flow naturescapes" not in flat, (
                f"{name}.md hard-codes this channel's opening identity"
            )

    def test_channel_declares_the_three_role_hierarchy(self) -> None:
        if not BRAND.is_file():
            pytest.skip("channel_0001/BRAND.md not present")
        brand = _flat(BRAND.read_text(encoding="utf-8"))
        assert "Brand signature" in brand
        assert "Welcome message" in brand
        assert "Episode line" in brand
        low = brand.lower()
        assert "not the headline" in low, (
            "BRAND.md must state the channel name is not the headline"
        )
        assert "largest" in low

    def test_riverflow_composition_exposes_three_independent_roles(self) -> None:
        """The composition must accept the hierarchy as separate props."""
        source = ROOT / "remotion-composer" / "src" / "RiverFlowOpening.tsx"
        if not source.is_file():
            pytest.skip("RiverFlowOpening.tsx not present")
        text = source.read_text(encoding="utf-8")
        for role in ("brandSignature", "welcomeMessage", "episodeLine"):
            assert role in text, f"RiverFlowOpening has no {role} prop"
        assert "videoSrc" in text, "the opening has no footage bed prop"

    def test_episode_copy_is_not_hardcoded_into_the_composition(self) -> None:
        """"Flow Into Calm" may be a default; it must not be the only value."""
        source = ROOT / "remotion-composer" / "src" / "RiverFlowOpening.tsx"
        if not source.is_file():
            pytest.skip("RiverFlowOpening.tsx not present")
        text = source.read_text(encoding="utf-8")
        assert "Flow Into Calm" not in text, (
            "episode copy must come from props, not be baked into the component"
        )


# ==========================================================================
# 23-25, 30. Delivery contracts
# ==========================================================================


class TestDeliveryContracts:
    def test_rejected_native_audio_cannot_reach_the_master(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        edit = _flat(directors["edit-director"])
        compose = _flat(directors["compose-director"])
        assert "REJECT" in edit and "contribute nothing" in edit
        assert "-map 0:v -map 1:a" in compose or "0:v + 1:a" in compose
        assert "never map" in compose.lower() or "Never map" in compose

        focus = " ".join(_stage(manifest, "compose")["review_focus"])
        assert "EXACTLY one audio stream" in focus

    def test_audio_duration_must_match_the_timeline(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        edit = _flat(directors["edit-director"])
        assert "Duration comes from the timeline" in edit
        assert "never pick a round number" in edit.lower() or \
               "never a round number" in edit.lower()
        criteria = " ".join(_stage(manifest, "edit")["success_criteria"])
        assert "Recorded mix duration equals the mixed file's real duration" in criteria

    def test_true_peak_is_verified_on_the_encoded_file(
        self, manifest: dict, directors: dict[str, str]
    ) -> None:
        compose = _flat(directors["compose-director"])
        assert "Sample peak is not true peak" in compose
        assert "encoded" in compose.lower()
        focus = " ".join(_stage(manifest, "compose")["review_focus"])
        assert "ENCODED file" in focus

    def test_final_fade_is_verified_in_the_delivered_file(
        self, manifest: dict
    ) -> None:
        focus = " ".join(_stage(manifest, "compose")["review_focus"])
        assert "Final fade verified present in the delivered file" in focus

    def test_operator_package_is_exactly_three_files(self, manifest: dict) -> None:
        criteria = " ".join(_stage(manifest, "publish")["success_criteria"])
        assert "exactly three operator files" in criteria
        assert "final.mp4" in criteria
        assert "thumbnail.jpg" in criteria
        assert "publish.txt" in criteria


# ==========================================================================
# 26-27. FFmpeg runtime
# ==========================================================================


class TestFFmpegRuntime:
    def test_no_project_local_ffmpeg_reference_in_tracked_files(self) -> None:
        """The removed distribution must not come back through a path string."""
        import subprocess

        listing = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True
        )
        if listing.returncode != 0:
            pytest.skip("not a git checkout")

        # This module names the markers in order to forbid them; scanning it
        # would make the check fail on its own definition.
        self_path = Path(__file__).resolve()

        offenders: list[str] = []
        for name in listing.stdout.splitlines():
            path = ROOT / name
            if not path.is_file() or path.suffix in {".png", ".jpg", ".mp4", ".ico"}:
                continue
            if path.resolve() == self_path:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(marker in text for marker in REMOVED_FFMPEG_MARKERS):
                offenders.append(name)
        assert not offenders, (
            f"files still reference the removed project-local FFmpeg: {offenders}"
        )

    def test_helpers_resolve_ffmpeg_only_through_path(self) -> None:
        source = (LIB / "ffmpeg_runtime.py").read_text(encoding="utf-8")
        assert "shutil.which" in source, "the resolver must use PATH lookup"
        assert "Tools" not in source.replace("tools", ""), (
            "the resolver must not name a project directory"
        )

    @pytest.mark.parametrize(
        "module", ("camera_motion.py", "stem_balance.py", "transition_audit.py")
    )
    def test_helpers_do_not_define_their_own_resolver(self, module: str) -> None:
        source = (LIB / module).read_text(encoding="utf-8")
        assert "ffmpeg_runtime" in source, (
            f"lib/{module} must use the shared PATH resolver"
        )
        assert ".exe" not in source, f"lib/{module} hard-codes a Windows binary name"

    def test_ffmpeg_and_ffprobe_resolve_on_path(self) -> None:
        """The production runtime contract: `cmd:ffmpeg`, `cmd:ffprobe`."""
        from lib.ffmpeg_runtime import FFmpegNotAvailable, ffmpeg_path, ffprobe_path

        if shutil.which("ffmpeg") is None:
            pytest.skip("ffmpeg not installed in this environment")
        for resolve in (ffmpeg_path, ffprobe_path):
            try:
                resolved = resolve()
            except FFmpegNotAvailable as exc:  # pragma: no cover
                pytest.fail(str(exc))
            assert Path(resolved).is_file()
            assert "ffmpeg-7.1.1-full_build" not in resolved.lower(), (
                "resolved inside the removed project-local distribution"
            )

    def test_missing_ffmpeg_raises_a_useful_error(self, monkeypatch) -> None:
        from lib import ffmpeg_runtime

        monkeypatch.setattr(ffmpeg_runtime.shutil, "which", lambda _: None)
        with pytest.raises(ffmpeg_runtime.FFmpegNotAvailable, match="not found"):
            ffmpeg_runtime.ffmpeg_path()
        assert ffmpeg_runtime.is_available() is False

    def test_compose_director_documents_the_path_contract(
        self, directors: dict[str, str]
    ) -> None:
        compose = _flat(directors["compose-director"])
        assert "cmd:ffmpeg" in compose
        assert "no project-local FFmpeg" in compose

    def test_production_does_not_depend_on_nvenc(
        self, directors: dict[str, str]
    ) -> None:
        compose = _flat(directors["compose-director"]).lower()
        assert "cpu `libx264` is the production path" in compose
        assert "not a blocker" in compose


# ---------------------------------------------------------------------------
# One workspace layout: the one init_project() creates
# ---------------------------------------------------------------------------


class TestCanonicalWorkspace:
    """Footage goes where OpenMontage's own project layout puts it.

    Both completed productions stored footage in `assets/video/`, while some
    instructions still named a legacy `visuals/` folder from the retired
    template. A download list pointing at the wrong folder costs a human the
    download, so the Directors must name the folders `init_project()` creates.
    """

    def test_init_project_creates_the_folders_the_directors_name(self, tmp_path, directors):
        from lib.checkpoint import init_project

        project = init_project("channel_9999__video_9999", title="layout check",
                               pipeline_type="relaxation", pipeline_dir=tmp_path)
        for sub in ("assets/video", "assets/audio", "assets/music"):
            assert (project / sub).is_dir()
            assert f"`{sub}/`" in directors["asset-director"]
        assert "assets/video/" in directors["procurement-director"]

    def test_no_relaxation_director_names_a_legacy_media_folder(self, directors):
        for name, text in directors.items():
            for legacy in ("`visuals/`", "`music/`", "`sfx/`", "`overlays/`"):
                if legacy in text:
                    assert "do not create" in _flat(text).lower(), (
                        f"{name}.md tells an agent to use legacy folder {legacy}"
                    )


class TestOpeningCanvasSafeguard:
    def test_compose_blocks_a_mismatched_opening_canvas(self, manifest, directors):
        compose = _flat(directors["compose-director"])
        # Broadened from canvas to format and colour: the executable check.
        assert "Match the format — a mismatch is a blocker." in compose
        assert "compare_segments(opening, body, contract)` must return `[]`" in compose
        assert "Never scale, pad or concatenate mismatched segments" in compose
        stage = next(s for s in manifest["stages"] if s["name"] == "compose")
        focus = " ".join(stage["review_focus"]).lower()
        assert "a mismatch is a blocker" in focus

    def test_opening_composition_fails_instead_of_falling_back(self):
        src = (ROOT / "remotion-composer" / "src" / "RiverFlowOpening.tsx").read_text(
            encoding="utf-8")
        assert "Fall back to 1080p if the bed cannot be probed" not in src
        assert "could not read its bed" in src and "throw new Error" in src
