"""Phase 1 acceptance: the approved plan, not the caller, bounds every paid call.

Three productions are planned by the proposal stage's own `plan_paid_audio`
(priced offline by the real V6 and ElevenLabs tools): the 2-minute validation,
a 10-minute piece and a 2-hour River-Flow-scale piece (55 min unique). Music
runs through a fake provider that writes real files; SFX and every bypass
attempt use the REAL `ElevenLabsSFX` and `SunoMusic` tools with only the HTTP
layer faked. The session network guard blocks any real connection; nothing
is spent.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import paid_call_guard
from lib.relaxation_policy import (
    approved_budget_tracker,
    approved_music_limits,
    approved_sfx_limits,
    generate_music_programme,
    generate_sfx_source,
    load_sfx_ledger,
    plan_paid_audio,
    record_music_review,
    record_sfx_review,
)
from tests.contracts.test_relaxation_paid_music_safety import (
    CALM,
    INCIDENT_TAGS,
    SCREEN,
    FakeMusic,
    accept_all,
    probe,
    substring_screen,
)
from tests.tools.test_suno_music import FakeSuno
from tools.audio import elevenlabs_sfx as sfx_module
from tools.audio import suno_music
from tools.audio.elevenlabs_sfx import ElevenLabsSFX
from tools.audio.suno_music import SunoMusic
from tools.cost_tracker import ApprovalRequiredError, CostTracker

SFX_SOURCES = [
    {"purpose": "principal bed, movement one (loop)", "duration_seconds": 30, "count": 1,
     "loop": True},
    {"purpose": "principal bed, movement two (loop)", "duration_seconds": 30, "count": 1,
     "loop": True},
    {"purpose": "occasional detail one-shot", "duration_seconds": 5, "count": 2, "loop": False},
]

#: (label, runtime s, unique music s, music retry allowance, both-good calls)
PRODUCTIONS = [
    ("2-min", 120, 120, 1.0, 1),
    ("10-min", 600, 600, 0.3, 1),
    ("2-hour", 7200, 3300, 0.3, 5),
]


@pytest.fixture(autouse=True)
def offline_pricing(monkeypatch):
    for model in ("V6", "V6_WILD", "V6_MINI"):
        monkeypatch.delenv(f"SUNO_CREDITS_PER_GENERATION_{model}", raising=False)
    monkeypatch.delenv(sfx_module.PRICE_ENV, raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-key")
    monkeypatch.setenv("SUNO_API_KEY", "suno-test-key")
    suno_music._PRICING_MISMATCHES.clear()
    yield
    suno_music._PRICING_MISMATCHES.clear()


def approved(runtime, unique, retry, budget=2.0):
    plan = plan_paid_audio(
        target_duration_seconds=runtime,
        music={"tool": "suno_music",
               "tool_inputs": {"model": "V6", "custom_mode": True, "instrumental": True},
               "unique_music_seconds": unique, "candidates_per_generation": 2,
               "accepted_per_generation": 1, "retry_allowance": retry},
        sfx={"tool": "elevenlabs_sfx", "tool_inputs": {"model_id": "eleven_text_to_sound_v2"},
             "sources": SFX_SOURCES, "retry_allowance": 0.4},
    )
    return {"approval": {"status": "approved", "approved_budget_usd": budget},
            "cost_estimate": plan["cost_estimate"],
            "metadata": {"paid_audio_plan": plan["metadata"]}}


def music_inputs(project):
    return {"model": "V6", "custom_mode": True, "instrumental": True, "prompt": "calm piano",
            "duration_seconds": 360.0,
            "output_path": str(project / "assets" / "music" / "piece.mp3")}


def music(project, packet, tool, evaluate=accept_all, **kw):
    tracker = approved_budget_tracker(packet, project)
    return generate_music_programme(project_dir=project, proposal_packet=packet, tracker=tracker,
                                    tool=tool, inputs=music_inputs(project), evaluate=evaluate,
                                    screen=SCREEN, probe=probe, **kw), tracker


def sfx_inputs(project, source, name, **extra):
    spec = SFX_SOURCES[source - 1]
    return {"prompt": "continuous close water, matched to the picture",
            "model_id": "eleven_text_to_sound_v2", "duration_seconds": spec["duration_seconds"],
            "loop": spec["loop"], "output_path": str(project / "assets" / "audio" / name),
            **extra}


def _audio_response(status=200):
    from unittest.mock import MagicMock

    r = MagicMock()
    r.status_code = status
    r.content = b"ID3-fake-sfx" if status == 200 else b""
    r.json.return_value = {"detail": "nope"}
    r.text = "nope"
    r.headers = {}
    return r


def sfx(project, packet, source, name, post=None, **extra):
    tool = ElevenLabsSFX()
    tool._probe_duration = staticmethod(lambda path: None)
    tracker = approved_budget_tracker(packet, project)
    post = post or patch("requests.post", return_value=_audio_response())
    with post as mocked:
        out = generate_sfx_source(project_dir=project, proposal_packet=packet, tracker=tracker,
                                  tool=tool, inputs=sfx_inputs(project, source, name, **extra),
                                  source=source)
    return out, tracker, mocked


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "channel_9999__video_0001"
    path.mkdir()
    return path


# --------------------------------------------------------------------------
# The approved plans themselves
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label,runtime,unique,retry,_calls", PRODUCTIONS)
def test_limits_are_the_approved_plan(label, runtime, unique, retry, _calls):
    packet = approved(runtime, unique, retry)
    limits = approved_music_limits(packet, "suno_music")
    plan = packet["metadata"]["paid_audio_plan"]["music"]
    assert limits["target_seconds"] == unique
    assert limits["seconds_per_generation"] == 360.0
    assert (limits["base_requests"], limits["retry_requests"]) == \
        (plan["base_requests"], plan["retry_requests"])
    assert limits["allocation_usd"] == pytest.approx(plan["estimated_usd"])
    sfx_limits = approved_sfx_limits(packet, "elevenlabs_sfx")
    assert sfx_limits["base_generations"] == 4 and sfx_limits["retry_generations"] == 2


# --------------------------------------------------------------------------
# Music outcomes at every production length
# --------------------------------------------------------------------------


@pytest.mark.parametrize("label,runtime,unique,retry,both_good_calls", PRODUCTIONS)
class TestMusicAcrossProductions:
    def test_both_candidates_accepted(self, project, label, runtime, unique, retry,
                                      both_good_calls):
        tool = FakeMusic([[(359.9, CALM), (359.5, CALM)]] * 20, name="suno_music")
        ledger, tracker = music(project, approved(runtime, unique, retry), tool)
        assert tool.calls == both_good_calls and ledger["stop"]["reason"] == "target_met"
        assert ledger["accepted_seconds"] >= unique
        assert tracker.budget_spent_usd == pytest.approx(0.06 * both_good_calls)

    def test_one_candidate_accepted_each_time(self, project, label, runtime, unique, retry,
                                              both_good_calls):
        batch = [(359.9, CALM), (359.5, "piano with a soft choir")]
        tool = FakeMusic([batch] * 20, name="suno_music")
        packet = approved(runtime, unique, retry)
        base = approved_music_limits(packet, "suno_music")["base_requests"]
        ledger, _ = music(project, packet, tool)
        needed = -(-unique // 359.9)
        assert tool.calls == min(needed, base)
        if needed <= base:
            assert ledger["stop"]["reason"] == "target_met"
        else:
            assert ledger["stop"]["reason"] == "retry_requires_operator_authorization"

    def test_both_rejected_pauses_after_one_call(self, project, label, runtime, unique, retry,
                                                 both_good_calls):
        bad = [(359.9, "lead vocals and piano"), (359.5, "choir over strings")]
        tool = FakeMusic([bad] * 20, name="suno_music")
        ledger, tracker = music(project, approved(runtime, unique, retry), tool)
        assert tool.calls == 1 and tracker.budget_spent_usd == pytest.approx(0.06)
        assert ledger["stop"]["reason"] == "operator_review_required"

    def test_uncertain_screening_pauses_after_one_call(self, project, label, runtime, unique,
                                                       retry, both_good_calls):
        tool = FakeMusic([[(359.9, "instrumental, wordless choir"), (359.5, CALM)]] * 20,
                         name="suno_music")
        ledger, _ = music(project, approved(runtime, unique, retry), tool)
        assert tool.calls == 1, "never a second paid call while a result is uncertain"
        outcomes = [c["outcome"] for c in ledger["generations"][0]["candidates"]]
        assert outcomes == ["uncertain", "accepted"]
        assert ledger["accepted_seconds"] == pytest.approx(359.5), "uncertain counts zero"
        if unique <= 359.5:   # the accepted candidate alone meets the target: nothing to buy
            assert ledger["stop"]["reason"] == "target_met"
        else:
            assert ledger["stop"]["reason"] == "operator_review_required"
            assert ledger["stop"]["detail"]["reasons"] == ["uncertain_candidates"]

    def test_sing_inside_phrasing_pauses_after_one_call(self, project, label, runtime, unique,
                                                        retry, both_good_calls):
        tool = FakeMusic([[(359.952, INCIDENT_TAGS[0]), (359.544, INCIDENT_TAGS[1])]] * 20,
                         name="suno_music")
        ledger, tracker = music(project, approved(runtime, unique, retry), tool,
                                evaluate=substring_screen)
        assert tool.calls == 1 and tracker.budget_spent_usd == pytest.approx(0.06)
        assert {c["criterion"] for c in ledger["generations"][0]["candidates"]} == \
            {"unsubstantiated_rejection"}

    def test_interruption_and_resume_never_pays_twice(self, project, label, runtime, unique,
                                                      retry, both_good_calls):
        tool = FakeMusic([[(359.9, CALM), (359.5, CALM)]] * 20, name="suno_music",
                         interrupt_on=1)
        packet = approved(runtime, unique, retry)
        with pytest.raises(KeyboardInterrupt):
            music(project, packet, tool)
        ledger, tracker = music(project, packet, tool)
        assert tool.fetches == 1, "the interrupted, billed task is fetched for free"
        assert tool.calls == both_good_calls, "no paid call was repeated"
        assert ledger["stop"]["reason"] == "target_met"
        assert tracker.budget_spent_usd == pytest.approx(0.06 * both_good_calls)

    def test_a_completed_target_makes_zero_further_calls(self, project, label, runtime, unique,
                                                         retry, both_good_calls):
        tool = FakeMusic([[(359.9, CALM), (359.5, CALM)]] * 20, name="suno_music")
        packet = approved(runtime, unique, retry)
        music(project, packet, tool)
        again = FakeMusic([[(359.9, CALM), (359.5, CALM)]] * 20, name="suno_music")
        ledger, _ = music(project, packet, again)
        assert again.calls == 0 and ledger["stop"]["reason"] == "target_met"

    def test_a_pricing_mismatch_stops_music(self, project, label, runtime, unique, retry,
                                            both_good_calls):
        tool = FakeMusic([[(100.0, CALM)]] * 20, name="suno_music", mismatch_on=1)
        ledger, _ = music(project, approved(runtime, unique, retry), tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "pricing_mismatch"


def test_a_legitimate_retry_follows_the_explicit_approval(project):
    """2-min plan, 1 + 1: the retry happens only after the operator authorises it."""
    short = [(33.5, CALM), (17.8, CALM)]
    tool = FakeMusic([short, [(359.9, CALM), (359.5, CALM)]], name="suno_music")
    packet = approved(120, 120, 1.0)
    ledger, _ = music(project, packet, tool)
    assert tool.calls == 1 and ledger["stop"]["reason"] == "retry_requires_operator_authorization"
    record_music_review(project, request=1, reviewer="operator", note="one retry approved",
                        authorize_next_request=True)
    ledger, _ = music(project, packet, tool)
    assert tool.calls == 2 and ledger["stop"]["reason"] == "target_met"
    ledger, _ = music(project, packet, tool)
    assert tool.calls == 2, "the ceiling (1 + 1) is spent; nothing more"


def test_music_never_touches_the_sfx_allocation(project):
    packet = approved(120, 120, 1.0, budget=5.0)
    music_limit = approved_music_limits(packet, "suno_music")["allocation_usd"]
    tool = FakeMusic([[(100.0, CALM)]] * 5, name="suno_music", charge=music_limit)
    record_music_review(project, request=1, reviewer="operator", note="go on",
                        authorize_next_request=True)
    ledger, tracker = music(project, packet, tool)
    assert tool.calls == 1 and ledger["stop"]["reason"] == "tool_allocation_exhausted"
    assert tracker.usable_budget_usd > 1.0


# --------------------------------------------------------------------------
# SFX: approved sources, retries, allocation, interruption - real tool
# --------------------------------------------------------------------------


class TestSfx:
    def test_each_approved_source_generates_through_the_real_tool(self, project):
        packet = approved(120, 120, 1.0)
        for source, name in ((1, "bed_a.mp3"), (2, "bed_b.mp3"), (3, "detail_1.mp3"),
                             (3, "detail_2.mp3")):
            out, tracker, post = sfx(project, packet, source, name)
            assert out["generated"] is True, out
            assert post.call_count == 1
        ops = [e["operation"] for e in tracker.entries if e["status"] == "completed"]
        assert ops == ["SFX source 1 attempt 1", "SFX source 2 attempt 1",
                       "SFX source 3 attempt 1", "SFX source 3 attempt 2"]

    def test_an_extra_generation_needs_an_authorised_retry(self, project):
        packet = approved(120, 120, 1.0)
        sfx(project, packet, 1, "bed_a.mp3")
        out, _, post = sfx(project, packet, 1, "bed_a_v2.mp3")
        assert out["generated"] is False and post.call_count == 0
        assert out["reason"] == "retry_requires_operator_authorization"
        record_sfx_review(project, reviewer="operator", note="seam audible, one retry",
                          authorize_retry_source=1)
        out, _, post = sfx(project, packet, 1, "bed_a_v2.mp3")
        assert out["generated"] is True and post.call_count == 1
        out, _, post = sfx(project, packet, 1, "bed_a_v3.mp3")
        assert out["reason"] == "retry_requires_operator_authorization" and post.call_count == 0

    def test_the_retry_allowance_is_a_hard_ceiling(self, project):
        packet = approved(120, 120, 1.0)
        for k, name in enumerate(("a.mp3", "b.mp3", "c.mp3")):
            if k:
                record_sfx_review(project, reviewer="operator", note="retry",
                                  authorize_retry_source=1)
            out, _, _ = sfx(project, packet, 1, name)
            assert out["generated"] is True
        record_sfx_review(project, reviewer="operator", note="retry", authorize_retry_source=1)
        out, _, post = sfx(project, packet, 1, "d.mp3")
        assert out["reason"] == "retry_allowance_exhausted" and post.call_count == 0

    def test_an_unapproved_source_or_a_different_request_is_refused(self, project):
        packet = approved(120, 120, 1.0)
        tool = ElevenLabsSFX()
        tracker = approved_budget_tracker(packet, project)
        with patch("requests.post", return_value=_audio_response()) as post:
            out = generate_sfx_source(project_dir=project, proposal_packet=packet,
                                      tracker=tracker, tool=tool,
                                      inputs=sfx_inputs(project, 1, "x.mp3"), source=9)
            assert out["reason"] == "source_not_approved"
            longer = generate_sfx_source(
                project_dir=project, proposal_packet=packet, tracker=tracker, tool=tool,
                inputs=sfx_inputs(project, 1, "y.mp3", duration_seconds=30.0, loop=False),
                source=1)
            assert longer["reason"] == "request_differs_from_approved_source"
        post.assert_not_called()

    def test_sfx_cannot_overwrite_an_existing_file(self, project):
        packet = approved(120, 120, 1.0)
        (project / "assets" / "audio").mkdir(parents=True)
        (project / "assets" / "audio" / "bed_a.mp3").write_bytes(b"paid earlier")
        out, _, post = sfx(project, packet, 1, "bed_a.mp3")
        assert out["reason"] == "output_exists_or_missing" and post.call_count == 0
        assert (project / "assets" / "audio" / "bed_a.mp3").read_bytes() == b"paid earlier"

    def test_an_interrupted_sfx_call_blocks_until_settled(self, project):
        packet = approved(120, 120, 1.0)
        with pytest.raises(KeyboardInterrupt):
            sfx(project, packet, 1, "bed_a.mp3",
                post=patch("requests.post", side_effect=KeyboardInterrupt))
        out, _, post = sfx(project, packet, 2, "bed_b.mp3")
        assert out["reason"] == "records_inconsistent" and post.call_count == 0
        assert out["detail"][0]["issue"] == "charge_outcome_unknown"
        record_sfx_review(project, reviewer="operator", note="dashboard shows the charge",
                          operation="SFX source 1 attempt 1", charge_outcome="charged")
        out, tracker, post = sfx(project, packet, 2, "bed_b.mp3")
        assert out["generated"] is True and post.call_count == 1
        again, _, post = sfx(project, packet, 1, "bed_a_v2.mp3")
        assert again["reason"] == "retry_requires_operator_authorization", \
            "the interrupted attempt used source 1's approved generation"

    def test_a_transport_failure_is_never_silently_repeated(self, project):
        import requests

        packet = approved(120, 120, 1.0)
        out, tracker, _ = sfx(project, packet, 1, "bed_a.mp3",
                              post=patch("requests.post", side_effect=requests.Timeout("slow")))
        assert out["reason"] == "provider_failure"
        assert load_sfx_ledger(project)["calls"][0]["charge_status"] == "unknown"
        out, _, post = sfx(project, packet, 2, "bed_b.mp3")
        assert out["reason"] == "records_inconsistent" and post.call_count == 0

    def test_sfx_never_touches_the_music_allocation(self, project):
        packet = approved(120, 120, 1.0, budget=5.0)
        allocation = approved_sfx_limits(packet, "elevenlabs_sfx")["allocation_usd"]
        tracker = approved_budget_tracker(packet, project)
        spend = tracker.estimate("elevenlabs_sfx", "SFX source 1 attempt 1", allocation)
        tracker.reserve(spend)
        tracker.reconcile(spend, allocation)
        from lib.relaxation_policy import _save_sfx_ledger

        _save_sfx_ledger(project, {"version": 1, "reviews": [], "calls": [
            {"operation": "SFX source 1 attempt 1", "status": "completed",
             "charge_status": "charged"}]})
        out, tracker, post = sfx(project, packet, 2, "bed_b.mp3")
        assert out["reason"] == "tool_allocation_exhausted" and post.call_count == 0
        assert tracker.usable_budget_usd > 1.0, "the cap had room; SFX's allocation did not"


# --------------------------------------------------------------------------
# Direct-call bypass attempts against the real tools
# --------------------------------------------------------------------------


class TestBypassAttempts:
    @pytest.fixture
    def relaxation_project(self, tmp_path, monkeypatch):
        import lib.events as events

        root = tmp_path / "projects"
        monkeypatch.setattr(events, "PROJECTS_DIR", root)
        project = root / "channel_9999__video_0002"
        project.mkdir(parents=True)
        (project / "project.json").write_text(json.dumps({"pipeline_type": "relaxation"}))
        return project

    def suno_inputs(self, project):
        return {**music_inputs(project), "output_path": str(project / "assets/music/x.mp3")}

    def test_direct_execute_is_refused(self, relaxation_project):
        fake = FakeSuno()
        with patch("requests.post", side_effect=fake.post), \
                patch("requests.get", side_effect=fake.get):
            result = SunoMusic().execute(self.suno_inputs(relaxation_project))
        assert result.success is False and fake.posts == [] and fake.gets == []
        with patch("requests.post", return_value=_audio_response()) as post:
            result = ElevenLabsSFX().execute(sfx_inputs(relaxation_project, 1, "a.mp3"))
        assert result.success is False
        post.assert_not_called()

    def test_a_plain_cost_tracker_is_refused(self, relaxation_project):
        from lib.config_model import BudgetMode

        tracker = CostTracker(budget_total_usd=10, reserve_pct=0, mode=BudgetMode.CAP,
                              single_action_approval_usd=10,
                              cost_log_path=relaxation_project / "cost_log.json")
        tracker.approve_tool("suno_music")
        fake = FakeSuno()
        with patch("requests.post", side_effect=fake.post), \
                patch("requests.get", side_effect=fake.get):
            result = tracker.run_tool(SunoMusic(), self.suno_inputs(relaxation_project))
        assert result.success is False and fake.posts == []
        assert result.cost_usd == 0.0, "refused before submit: nothing charged"

    def test_the_approved_tracker_without_a_grant_is_refused(self, relaxation_project):
        tracker = approved_budget_tracker(approved(120, 120, 1.0), relaxation_project)
        fake = FakeSuno()
        with patch("requests.post", side_effect=fake.post), \
                patch("requests.get", side_effect=fake.get):
            with pytest.raises(ApprovalRequiredError):
                tracker.run_tool(SunoMusic(), self.suno_inputs(relaxation_project),
                                 operation="music generation 1")
        assert fake.posts == []

    def test_the_policy_path_reaches_the_real_tool(self, relaxation_project):
        """The one sanctioned path works end to end with the real Suno tool."""
        packet = approved(120, 120, 1.0)
        tool = SunoMusic()
        tool._POLL_INTERVAL = 0
        tracks = [{"id": f"a{i}", "audio_url": f"https://cdn.example/{i}.mp3",
                   "title": "Take", "tags": CALM, "duration": 359.9} for i in range(2)]
        fake = FakeSuno(tracks=tracks, credits=(976, 964))
        tracker = approved_budget_tracker(packet, relaxation_project)
        with patch("requests.post", side_effect=fake.post), \
                patch("requests.get", side_effect=fake.get):
            ledger = generate_music_programme(
                project_dir=relaxation_project, proposal_packet=packet, tracker=tracker,
                tool=tool, inputs=music_inputs(relaxation_project), evaluate=accept_all,
                screen=SCREEN, probe=lambda path: 359.9)
        assert len(fake.posts) == 1 and ledger["stop"]["reason"] == "target_met"
        assert tracker.budget_spent_usd == pytest.approx(0.06)
        record = tool.pending_task_record(
            relaxation_project / "assets" / "music" / "piece_g01.mp3")
        assert record["status"] == "completed"

    def test_other_pipelines_keep_upstream_behaviour(self, relaxation_project):
        other = relaxation_project.parent / "explainer_0001"
        other.mkdir()
        (other / "project.json").write_text(json.dumps({"pipeline_type": "explainer"}))
        governed, _, _ = paid_call_guard.governing_project(
            {"output_path": str(other / "assets" / "m.mp3")})
        assert governed is False
