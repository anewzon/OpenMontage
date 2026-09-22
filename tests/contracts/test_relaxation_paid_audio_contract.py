"""Contracts for the relaxation pipeline's paid audio and budget governance.

Generated music (`suno_music`) and generated SFX (`elevenlabs_sfx`) are paid.
These tests hold four lines:

1. They are ordinary registry tools used in an existing stage - no new stage,
   no parallel registry, router or budget system.
2. Every paid call is priced at proposal, approved by the operator, and run
   through OpenMontage's own CostTracker in cap mode - and it STOPS at the cap.
3. The generic pipeline stays channel-neutral: no fixed SFX taxonomy, no
   channel's music policy, and native clip audio still wins where it is good.
4. The existing human Envato gate, mixer, renderer and three-file output stay
   exactly as they were.

No test here touches the network or spends anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from lib.config_model import BudgetMode
from lib.relaxation_policy import (
    BudgetNotApproved,
    PaidCostUnavailable,
    approved_budget_tracker,
    budget_summary,
    plan_paid_audio,
)
from schemas.artifacts import load_schema, validate_artifact
from tools.base_tool import BaseTool, ToolResult
from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError, CostTracker

ROOT = Path(__file__).resolve().parents[2]
RELAX = ROOT / "skills" / "pipelines" / "relaxation"
MANIFEST = ROOT / "pipeline_defs" / "relaxation.yaml"
META = ROOT / "skills" / "meta"

PAID_AUDIO_TOOLS = ("suno_music", "elevenlabs_sfx")

#: Environmental sound categories a generic Director must never enumerate as
#: its own list. ("stream" is left out: it also means an audio stream.)
SOUND_CATEGORIES = ("water", "river", "waterfall", "forest", "bird", "wind", "rain", "fire", "ocean")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s*>\s?", "", text))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def directors() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in RELAX.glob("*.md")}


def _stage(manifest: dict, name: str) -> dict:
    return next(s for s in manifest["stages"] if s["name"] == name)


def _focus(manifest: dict, name: str) -> str:
    stage = _stage(manifest, name)
    return " ".join(stage.get("review_focus", []) + stage.get("success_criteria", [])).lower()


# --------------------------------------------------------------------------
# Test doubles: a paid tool that records whether it was executed
# --------------------------------------------------------------------------


class FakePaidTool(BaseTool):
    name = "fake_paid"
    capability = "music_generation"

    def __init__(self, price: float = 1.0, actual: float | None = None,
                 success: bool = True) -> None:
        self.price = price
        self.actual = price if actual is None else actual
        self.success = success
        self.calls = 0

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return self.price

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return ToolResult(success=self.success, cost_usd=self.actual)


class UnpricedTool(FakePaidTool):
    name = "unpriced"

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        raise ValueError("price unknown")


def _cap_tracker(tmp_path: Path, budget: float, tools=("fake_paid",)) -> CostTracker:
    tracker = CostTracker(
        budget_total_usd=budget, reserve_pct=0.0, single_action_approval_usd=float("inf"),
        require_approval_for_new_paid_tool=True, mode=BudgetMode.CAP,
        cost_log_path=tmp_path / "cost_log.json",
    )
    for name in tools:
        tracker.approve_tool(name)
    return tracker


# --------------------------------------------------------------------------
# 1. CostTracker.run_tool - estimate, reserve, execute, reconcile, stop
# --------------------------------------------------------------------------


class TestCostTrackerLifecycle:
    def test_paid_call_is_estimated_reserved_executed_and_reconciled(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 5.0)
        tool = FakePaidTool(price=1.0, actual=0.8)
        result = tracker.run_tool(tool, {}, operation="music: movement one")
        assert result.success and tool.calls == 1
        entry = tracker.entries[-1]
        assert entry["status"] == "completed"
        assert entry["estimated_usd"] == 1.0 and entry["actual_usd"] == 0.8
        assert entry["reserved_usd"] == 0.0
        assert tracker.budget_spent_usd == 0.8
        persisted = json.loads((tmp_path / "cost_log.json").read_text())
        assert persisted["entries"][-1]["status"] == "completed"

    def test_projected_overspend_stops_before_the_tool_runs(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 1.5)
        tool = FakePaidTool(price=1.0)
        tracker.run_tool(tool, {})
        with pytest.raises(BudgetExceededError):
            tracker.run_tool(tool, {})
        assert tool.calls == 1, "a refused reservation must never execute the tool"
        blocked = tracker.entries[-1]
        assert blocked["status"] == "refunded"
        assert "blocked before execution" in blocked["details"]

    def test_retries_are_charged_and_stop_at_the_cap(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 3.0)
        failing = FakePaidTool(price=1.0, actual=1.0, success=False)
        attempts = 0
        with pytest.raises(BudgetExceededError):
            while True:
                tracker.run_tool(failing, {}, operation="retry")
                attempts += 1
        assert attempts == 3
        assert tracker.budget_spent_usd == 3.0, "failed paid attempts still spend"
        assert all(e["status"] in ("failed", "refunded") for e in tracker.entries)

    def test_an_unpriced_call_is_never_made_or_recorded(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 5.0, tools=("unpriced",))
        tool = UnpricedTool()
        with pytest.raises(ValueError):
            tracker.run_tool(tool, {})
        assert tool.calls == 0 and tracker.entries == []

    def test_a_tool_outside_the_approved_plan_cannot_spend(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 5.0, tools=())
        tool = FakePaidTool(price=0.1)
        with pytest.raises(ApprovalRequiredError):
            tracker.run_tool(tool, {})
        assert tool.calls == 0

    def test_an_exception_mid_call_is_accounted_at_the_estimate(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 5.0)

        class Exploding(FakePaidTool):
            def execute(self, inputs):
                raise RuntimeError("provider vanished")

        with pytest.raises(RuntimeError):
            tracker.run_tool(Exploding(price=0.7), {})
        assert tracker.budget_spent_usd == 0.7

    def test_the_persisted_cost_log_is_schema_valid(self, tmp_path):
        tracker = _cap_tracker(tmp_path, 5.0)
        tracker.run_tool(FakePaidTool(price=0.5), {}, details="provider=fake model=x")
        with pytest.raises(BudgetExceededError):
            tracker.run_tool(FakePaidTool(price=9.0), {})
        validate_artifact("cost_log", json.loads((tmp_path / "cost_log.json").read_text()))

    def test_logs_written_before_the_schema_fix_still_load(self, tmp_path):
        path = tmp_path / "cost_log.json"
        path.write_text(json.dumps({
            "version": "1.0", "budget_total_usd": 2.0, "approved_tools": ["legacy"],
            "entries": [],
        }))
        tracker = CostTracker(cost_log_path=path, mode=BudgetMode.CAP,
                              single_action_approval_usd=5.0)
        entry = tracker.estimate("legacy", "op", 0.1)
        tracker.reserve(entry)


# --------------------------------------------------------------------------
# 2. The approved budget is the cap
# --------------------------------------------------------------------------


def _packet(status="approved", budget: Any = 1.0, tools=PAID_AUDIO_TOOLS) -> dict:
    return {
        "approval": {"status": status, **({} if budget is None else {"approved_budget_usd": budget})},
        "cost_estimate": {
            "total_estimated_usd": 0.5,
            "budget_verdict": "within_budget",
            "line_items": [
                *({"tool": t, "operation": "gen", "estimated_usd": 0.25} for t in tools),
                {"tool": "licensed_manual", "operation": "visual footage", "estimated_usd": 0.0},
            ],
        },
    }


class TestApprovedBudget:
    @pytest.mark.parametrize("status", ["pending", "rejected"])
    def test_no_paid_generation_without_an_approved_proposal(self, tmp_path, status):
        with pytest.raises(BudgetNotApproved):
            approved_budget_tracker(_packet(status=status), tmp_path)

    def test_no_paid_generation_without_an_approved_budget(self, tmp_path):
        with pytest.raises(BudgetNotApproved):
            approved_budget_tracker(_packet(budget=None), tmp_path)

    def test_tracker_is_capped_at_the_approved_budget(self, tmp_path):
        tracker = approved_budget_tracker(_packet(budget=0.75), tmp_path)
        assert tracker.mode == BudgetMode.CAP
        assert tracker.budget_total_usd == 0.75 and tracker.usable_budget_usd == 0.75
        assert (tmp_path / "cost_log.json").is_file()

    def test_a_call_bigger_than_the_whole_budget_is_a_cap_stop(self, tmp_path):
        tracker = approved_budget_tracker(_packet(budget=0.75), tmp_path)
        big = FakePaidTool(price=2.0)
        big.name = "suno_music"
        with pytest.raises(BudgetExceededError):
            tracker.run_tool(big, {})
        assert big.calls == 0

    def test_only_tools_in_the_approved_estimate_may_spend(self, tmp_path):
        tracker = approved_budget_tracker(_packet(tools=("suno_music",)), tmp_path)
        rogue = FakePaidTool(price=0.1)
        rogue.name = "elevenlabs_sfx"
        with pytest.raises(ApprovalRequiredError):
            tracker.run_tool(rogue, {})

    def test_a_resumed_log_takes_the_newly_approved_budget(self, tmp_path):
        approved_budget_tracker(_packet(budget=5.0), tmp_path)
        tracker = approved_budget_tracker(_packet(budget=0.5, tools=("suno_music",)), tmp_path)
        assert tracker.budget_total_usd == 0.5
        rogue = FakePaidTool(price=0.1)
        rogue.name = "elevenlabs_sfx"
        with pytest.raises(ApprovalRequiredError):
            tracker.run_tool(rogue, {})


# --------------------------------------------------------------------------
# 3. The proposal estimate
# --------------------------------------------------------------------------


def _music(**over) -> dict:
    base = dict(tool="suno_music", tool_inputs={"model": "V6", "custom_mode": True},
                unique_music_seconds=120, seconds_per_generation=130,
                candidates_per_generation=2, accepted_per_generation=1, retry_allowance=1.0)
    return {**base, **over}


def _sfx(**over) -> dict:
    base = dict(tool="elevenlabs_sfx", tool_inputs={"loop": True},
                sources=[{"purpose": "principal bed for movement one", "duration_seconds": 30, "count": 2},
                         {"purpose": "occasional detail", "duration_seconds": 5, "count": 3}],
                retry_allowance=0.5)
    return {**base, **over}


@pytest.fixture
def suno_priced(monkeypatch):
    monkeypatch.setenv("SUNO_CREDITS_PER_GENERATION", "12")


class TestProposalEstimate:
    def test_unconfirmed_music_pricing_blocks_the_plan(self, monkeypatch):
        monkeypatch.delenv("SUNO_CREDITS_PER_GENERATION", raising=False)
        monkeypatch.delenv("SUNO_CREDITS_PER_GENERATION_V6", raising=False)
        with pytest.raises(PaidCostUnavailable) as exc:
            plan_paid_audio(target_duration_seconds=120, music=_music())
        assert "SUNO_CREDITS_PER_GENERATION" in str(exc.value)

    def test_estimate_is_schema_valid_for_the_proposal_packet(self, suno_priced):
        plan = plan_paid_audio(target_duration_seconds=120, music=_music(), sfx=_sfx(),
                               budget_cap_usd=2.0)
        import jsonschema

        subschema = load_schema("proposal_packet")["properties"]["cost_estimate"]
        jsonschema.validate(plan["cost_estimate"], subschema)
        assert plan["cost_estimate"]["budget_verdict"] == "within_budget"

    def test_estimate_covers_music_sfx_retry_video_and_local_work(self, suno_priced):
        plan = plan_paid_audio(target_duration_seconds=120, music=_music(), sfx=_sfx())
        items = plan["cost_estimate"]["line_items"]
        ops = {i["operation"] for i in items}
        assert {"music generation", "music retry/rejection allowance", "SFX generation",
                "SFX retry allowance", "visual footage", "local mixing, render and QC"} <= ops
        video = next(i for i in items if i["operation"] == "visual footage")
        assert video["estimated_usd"] == 0 and "externally managed" in video["notes"]
        meta = plan["metadata"]
        assert meta["music"]["model"] == "V6"
        assert meta["sfx"]["generated_source_seconds"] == 75.0
        assert meta["retry_reserve_usd"] > 0
        assert meta["paid_tools"] == ["elevenlabs_sfx", "suno_music"]
        total = sum(i["estimated_usd"] for i in items)
        assert plan["cost_estimate"]["total_estimated_usd"] == pytest.approx(total)

    def test_music_is_a_programme_not_minute_for_minute(self, suno_priced):
        two_hours = 7200
        linear = plan_paid_audio(target_duration_seconds=two_hours,
                                 music=_music(unique_music_seconds=two_hours, retry_allowance=0))
        programme = plan_paid_audio(target_duration_seconds=two_hours,
                                    music=_music(unique_music_seconds=3300, retry_allowance=0))
        assert programme["metadata"]["music"]["reprised_seconds"] == two_hours - 3300
        assert (programme["metadata"]["music"]["base_requests"]
                < linear["metadata"]["music"]["base_requests"])

    def test_unique_music_cannot_exceed_the_production(self, suno_priced):
        with pytest.raises(ValueError):
            plan_paid_audio(target_duration_seconds=120, music=_music(unique_music_seconds=121))

    def test_retry_allowance_has_no_silent_default(self, suno_priced):
        with pytest.raises(ValueError):
            plan_paid_audio(target_duration_seconds=120, music=_music(retry_allowance=None))

    def test_sfx_is_priced_on_source_seconds_not_runtime(self):
        short = plan_paid_audio(target_duration_seconds=120, sfx=_sfx())
        long = plan_paid_audio(target_duration_seconds=18000, sfx=_sfx())
        assert short["metadata"]["sfx"]["estimated_usd"] == long["metadata"]["sfx"]["estimated_usd"]

    def test_a_production_with_no_paid_audio_costs_nothing(self):
        plan = plan_paid_audio(target_duration_seconds=600)
        assert plan["cost_estimate"]["total_estimated_usd"] == 0
        assert plan["metadata"]["paid_tools"] == []

    def test_operator_summary_carries_every_approval_field(self, suno_priced):
        text = budget_summary(plan_paid_audio(target_duration_seconds=120,
                                              music=_music(), sfx=_sfx()), 1.0)
        for label in ("Target duration:", "Music provider:", "Music programme target:",
                      "Expected music generations:", "Estimated music cost:",
                      "SFX provider:", "Expected generated SFX source duration:",
                      "Expected SFX generations:", "Estimated SFX cost:",
                      "Estimated paid API total:", "Retry reserve:",
                      "Maximum approved production budget: $1.00"):
            assert label in text


# --------------------------------------------------------------------------
# 4. Pipeline wiring: existing stages, existing tools, human gate intact
# --------------------------------------------------------------------------


class TestPipelineWiring:
    def test_no_new_stage_was_added_for_generation(self, manifest):
        assert [s["name"] for s in manifest["stages"]] == [
            "research", "proposal", "procurement", "assets",
            "scene_plan", "edit", "compose", "publish",
        ]

    def test_paid_audio_tools_are_available_only_in_the_assets_stage(self, manifest):
        for stage in manifest["stages"]:
            declared = set(stage.get("tools_available", [])) | set(stage.get("optional_tools", []))
            for tool in PAID_AUDIO_TOOLS:
                if stage["name"] == "assets":
                    assert tool in stage["tools_available"] and tool in stage["optional_tools"]
                else:
                    assert tool not in declared, f"{tool} leaked into {stage['name']}"

    def test_paid_audio_tools_are_ordinary_registry_tools(self):
        from tools.tool_registry import ToolRegistry

        registry = ToolRegistry()
        registry.discover()
        assert registry.get("suno_music").capability == "music_generation"
        assert registry.get("elevenlabs_sfx").capability == "sfx_generation"

    def test_no_parallel_registry_router_or_budget_module(self):
        for rel in ("tools", "lib"):
            for path in (ROOT / rel).rglob("*.py"):
                stem = path.stem.lower()
                assert not re.search(r"vidqwik.*(registry|router|budget|provider)", stem), path
                assert not re.search(r"(provider_router|budget_manager|sfx_selector|music_router)", stem), path

    def test_licensed_footage_stays_a_human_download(self, manifest):
        focus = _focus(manifest, "procurement")
        assert "no automated paid-stock downloading" in focus
        assert _stage(manifest, "procurement")["human_approval_default"] is True
        for path in list((ROOT / "tools").rglob("*.py")) + list((ROOT / "lib").rglob("*.py")):
            assert "elements.envato.com" not in path.read_text(encoding="utf-8", errors="ignore"), (
                f"{path} targets Envato Elements - licensed footage is downloaded by a person"
            )

    def test_asset_list_stays_an_employee_workflow(self, directors):
        proc = _flat(directors["procurement-director"])
        assert "ASSET_LIST.md" in proc and "Assets added, continue." in proc
        low = proc.lower()
        for field in ("purpose", "title", "item-page url", "subject", "camera movement",
                      "resolution", "usable duration", "avoid"):
            assert field in low, f"ASSET_LIST guidance lost {field!r}"
        assert "`visuals/`" in proc
        meta = (META / "asset-procurement.md").read_text(encoding="utf-8")
        assert "EMPLOYEE INSTRUCTIONS" in meta
        for field in ("SUBJECT:", "CAMERA MOVEMENT:", "USABLE DURATION NEEDED:", "AVOID:", "PUT IN:"):
            assert field in meta

    def test_generation_happens_after_the_footage_exists(self, directors):
        asset = _flat(directors["asset-director"]).lower()
        assert "after the real footage has been analysed" in asset
        proc = _flat(directors["procurement-director"]).lower()
        assert "music the plan generates is not procured here" in proc

    def test_mix_and_render_paths_are_unchanged(self, manifest):
        assert "audio_mixer" in _stage(manifest, "edit")["required_tools"]
        assert "video_compose" in _stage(manifest, "compose")["required_tools"]
        publish = _focus(manifest, "publish")
        assert "exactly three operator files" in publish


# --------------------------------------------------------------------------
# 5. Budget rules are in the gates and the Directors
# --------------------------------------------------------------------------


class TestBudgetRulesAreGated:
    def test_proposal_gate_prices_before_spending(self, manifest):
        focus = _focus(manifest, "proposal")
        assert "before any paid call" in focus
        assert "approved_budget_usd" in focus
        assert "cost estimate is 0.00" not in focus, "the zero-cost assumption must be gone"

    def test_assets_gate_enforces_the_cap(self, manifest):
        focus = _focus(manifest, "assets")
        for phrase in ("approved_budget_tracker().run_tool", "stops the run",
                       "retries are new paid calls", "candidate zero not accepted blindly",
                       "no silent provider substitution", "cost_log.json"):
            assert phrase in focus, phrase

    def test_no_director_still_claims_production_is_free(self, directors):
        for name, text in directors.items():
            assert "`cost_estimate` is **0.00 usd**" not in text.lower(), name
        ep = _flat(directors["executive-producer"]).lower()
        assert "no paid providers. budget 0.00" not in ep
        assert "paid generation only inside the approved budget" in ep

    def test_directors_forbid_bypassing_the_tracker(self, directors):
        asset = _flat(directors["asset-director"])
        assert "Never call a paid tool's `execute()` directly" in asset
        assert "BudgetExceededError` means STOP" in asset
        assert "A retry is a new paid call" in asset

    def test_proposal_director_uses_the_existing_schema_fields(self, directors):
        proposal = _flat(directors["proposal-director"])
        assert "plan_paid_audio" in proposal and "approval.approved_budget_usd" in proposal
        assert "never make an unpriced paid call" in proposal.lower()


# --------------------------------------------------------------------------
# 6. Channel-neutral: SFX taxonomy, music policy and native audio
# --------------------------------------------------------------------------


class TestChannelNeutrality:
    def test_no_director_enumerates_a_fixed_sfx_taxonomy(self, directors):
        for name, text in directors.items():
            for para in re.split(r"\n\s*\n", text):
                low = para.lower()
                if not re.search(r"\bsfx\b|ambience|soundscape|\bbeds?\b|audio layer", low):
                    continue
                hits = {c for c in SOUND_CATEGORIES if re.search(rf"\b{c}s?\b", low)}
                assert len(hits) < 2, (
                    f"{name}.md lists sound categories {sorted(hits)} in audio guidance - "
                    "which sounds an episode needs comes from BRAND.md and the footage"
                )

    def test_no_sfx_category_folders(self, directors):
        for name, text in directors.items():
            assert not re.search(r"sfx/[a-z_]+/", text, re.I), name

    def test_generated_audio_section_is_derived_not_listed(self, directors):
        asset = directors["asset-director"]
        section = asset.split("## Generated audio", 1)[1].split("\n## ", 1)[0]
        low = section.lower()
        for word in SOUND_CATEGORIES:
            assert not re.search(rf"\b{word}s?\b", low), f"generated-audio guidance names {word!r}"
        assert "there is no fixed set of sound" in _flat(low)
        assert "brand.md" in low

    def test_channel_music_policy_stays_in_the_channel(self, directors):
        code = (ROOT / "lib" / "relaxation_policy.py").read_text(encoding="utf-8").lower()
        for text in [*directors.values(), code]:
            low = _flat(text).lower()
            assert not re.search(r"50\s*[–-]\s*60 minutes", low)
            assert "principal water" not in low
            assert "river flow" not in low and "channel_0001" not in low

    def test_native_audio_still_takes_precedence(self, directors, manifest):
        asset = _flat(directors["asset-director"])
        assert "Native audio first." in asset
        assert "Do not generate an equivalent layer" in asset
        for cls in ("USE", "USE_AFTER_TREATMENT", "REJECT", "NO_AUDIO"):
            assert f"`{cls}`" in asset
        assert "equivalent library or generated bed" in _focus(manifest, "edit")
        edit = _flat(directors["edit-director"])
        assert "equivalent library or generated bed" in edit

    def test_music_programme_rules_are_in_the_edit(self, directors):
        edit = _flat(directors["edit-director"]).lower()
        assert "never repeat a complete mixed programme" in edit
        assert "no obvious short-track looping" in edit
        assert "phrase boundaries" in edit
        assert "never duplicate the whole mastered programme" in edit
        assert "metadata.music_programme" in edit
