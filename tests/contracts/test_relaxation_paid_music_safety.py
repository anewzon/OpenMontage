"""Phase 1 paid-audio safety: no screening mistake, interruption or bad count
can silently trigger another paid music generation.

Every provider here is a fake that writes real files into a temporary project
directory; nothing touches the network (tests/conftest.py blocks it anyway)
and nothing is spent. The incident class is reproduced from the real
channel_0001__video_0003 candidate tags: a substring screen matched ``sing``
inside ``phrasing`` and paid for a second generation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import pytest

from lib.relaxation_policy import (
    MUSIC_LEDGER,
    MusicLimitsUnavailable,
    approved_budget_tracker,
    approved_music_limits,
    decide_music_candidate,
    find_prohibited_terms,
    generate_music_programme,
    load_music_ledger,
    plan_paid_audio,
    reconcile_music_progress,
    record_music_review,
    screen_music_candidate,
)
from tools.audio import suno_music
from tools.base_tool import BaseTool, ToolResult

# The four real Suno V6 tag strings from channel_0001__video_0003 (2026-09-23),
# every one falsely rejected because "phrasing" contains "sing".
INCIDENT_TAGS = [
    "Ambient solo piano with warm soft ambient pads, smooth phrasing and controlled dynamics, "
    "slow unhurried pulse, restrained one-instrument-led arrangement, gradual rise toward a "
    "waterfall-like peak then settling into stillness",
    "Ambient, sparse production with smooth phrasing, controlled dynamics and restrained "
    "arrangement; instrumental; slow, unhurried rise and settling; few piano voices among warm "
    "soft ambient pads, natural space and gentle waterfall-like motion",
    "Ambient, intimate piano phrases over warm soft ambient pads, slow unhurried pulse, "
    "instrumental, smooth phrasing and controlled dynamics with a restrained arrangement, gradual "
    "widening toward a subtle waterfall-like flow before settling into quiet stillness",
    "Ambient, slow unhurried tempo, instrumental, gentle piano phrases with warm soft ambient "
    "pads, very gradual rise toward a waterfall-like peak, then settling into stillness, smooth "
    "phrasing, controlled dynamics, restrained natural arrangement",
]

#: A prohibited list written the way a channel's BRAND.md states one. Generic
#: fixture data - the pipeline itself names no channel's terms.
PROHIBITED = ["lyrics", "singing", "sing", "singer", "sung", "vocals", "vocal samples",
              "vocal chops", "choir", "spoken word", "spoken words", "rap"]
SCREEN = {"prohibited_terms": PROHIBITED, "fields": ["title", "tags"],
          "compliance_terms": ["instrumental"]}
ROOT = Path(__file__).resolve().parents[2]
CALM = "Ambient, instrumental, gentle piano with warm soft pads, smooth phrasing"
PRICE = 0.06


# --------------------------------------------------------------------------
# A fake paid music provider with the real tool's durable-record behaviour
# --------------------------------------------------------------------------


class FakeMusic(BaseTool):
    """Writes real candidate files (content = their length) and task records.

    ``batches[n-1]`` is the list of ``(seconds, tags)`` candidates the n-th
    PAID generation returns. Fetch (free) re-delivers a task without charging.
    """

    capability = "music_generation"
    provider = "test"
    input_schema = {"type": "object", "properties": {
        "duration_seconds": {"type": "number", "minimum": 10, "maximum": 360}}}

    def __init__(self, batches, *, name="fake_music", charge: Optional[float] = None,
                 interrupt_on=None, interrupt_before_task_on=None, timeout_on=None,
                 mismatch_on=None, unverified_on=None, fetch_fails=False, on_execute=None):
        self.name = name
        self.batches = list(batches)
        self.charge = PRICE if charge is None else charge
        self.interrupt_on = interrupt_on
        self.interrupt_before_task_on = interrupt_before_task_on
        self.timeout_on = timeout_on
        self.mismatch_on = mismatch_on
        self.unverified_on = unverified_on
        self.fetch_fails = fetch_fails
        self.on_execute = on_execute
        self.calls = 0          # PAID generations only
        self.fetches = 0
        self.tasks: dict[str, list] = {}

    def estimate_cost(self, inputs):
        return 0.0 if inputs.get("operation", "generate") != "generate" else PRICE

    def dry_run(self, inputs):
        return {}

    def pending_task_record(self, output_path):
        return suno_music.read_task_record(output_path)

    def execute(self, inputs):
        out = Path(inputs["output_path"])
        if inputs.get("operation") == "fetch":
            self.fetches += 1
            if self.fetch_fails:
                return ToolResult(success=False, error="task not found", data={})
            return self._deliver(out, self.tasks[inputs["task_id"]], inputs["task_id"], 0.0,
                                 keep=True)
        if self.on_execute:
            self.on_execute(inputs)
        self.calls += 1
        n = self.calls
        record = {"status": "submitting", "task_id": None, "output_path": str(out)}
        suno_music._write_json_atomic(suno_music.task_record_path(out), record)
        if self.interrupt_before_task_on == n:
            raise KeyboardInterrupt
        task_id = f"task-{n}"
        self.tasks[task_id] = self.batches[n - 1]
        record.update(status="submitted", task_id=task_id)
        suno_music._write_json_atomic(suno_music.task_record_path(out), record)
        if self.interrupt_on == n:
            raise KeyboardInterrupt  # the session dies while polling
        if self.timeout_on == n:
            record.update(status="failed", charge_status="charged")
            suno_music._write_json_atomic(suno_music.task_record_path(out), record)
            return ToolResult(success=False, cost_usd=self.charge,
                              error=f"timed out; recover with operation=fetch task_id={task_id}",
                              data={"task_id": task_id, "charge_status": "charged"})
        result = self._deliver(out, self.batches[n - 1], task_id, self.charge)
        if self.mismatch_on == n:
            result.data["pricing_mismatch"] = {"message": "expected 12 credits, charged 20."}
        if self.unverified_on == n:
            result.data["pricing_check"] = {"status": "unverified"}
        record.update(status="completed")
        suno_music._write_json_atomic(suno_music.task_record_path(out), record)
        return result

    def _deliver(self, out: Path, batch, task_id: str, cost: float, keep: bool = False):
        out.parent.mkdir(parents=True, exist_ok=True)
        cands = []
        for i, (seconds, tags) in enumerate(batch):
            path = out if i == 0 else out.with_name(f"{out.stem}__cand{i}{out.suffix}")
            if not (keep and path.exists()):
                path.write_text(str(seconds), encoding="utf-8")
            cands.append({"index": i, "path": str(path), "downloaded": True,
                          "title": f"take {i}", "tags": tags, "duration_seconds": 360})
        return ToolResult(success=True, cost_usd=cost,
                          data={"task_id": task_id, "candidates": cands,
                                "charge_status": "charged" if cost else "no_new_charge"})


def probe(path):
    """Reads the length the fake wrote; raises on files it did not write."""
    text = Path(path).read_text(encoding="utf-8")
    if text == "UNREADABLE":
        raise RuntimeError("ffprobe failed")
    return float(text)


def accept_all(cands):
    """A technical/creative evaluation that likes every screened candidate."""
    return {c["index"]: {"outcome": "accepted", "criterion": "passed_technical_and_creative",
                         "reason": "calm, even, fits the brief"}
            for c in cands if c["screen"]["outcome"] == "passed"}


def substring_screen(cands):
    """The faulty evaluator from video_0003: raw substring matching."""
    banned = ("vocal", "sing", "lyric", "choir", "voice")
    return {c["index"]: ({"accepted": False,
                          "reason": f"provider tags mention excluded content: {c['tags']}"}
                         if any(b in c["tags"].lower() for b in banned)
                         else {"accepted": True, "reason": "passes"}) for c in cands}


# --------------------------------------------------------------------------
# An approved proposal, as the proposal stage records it
# --------------------------------------------------------------------------


def proposal(*, tool="fake_music", target=600.0, base=2, retry=1, per_gen=360.0,
             budget=0.55, sfx_usd=0.32) -> dict[str, Any]:
    items = [
        {"tool": tool, "operation": "music generation", "quantity": base,
         "estimated_usd": round(base * PRICE, 4)},
        {"tool": tool, "operation": "music retry/rejection allowance", "quantity": retry,
         "estimated_usd": round(retry * PRICE, 4)},
    ]
    if sfx_usd:
        items.append({"tool": "elevenlabs_sfx", "operation": "SFX generation", "quantity": 5,
                      "estimated_usd": sfx_usd})
    return {
        "approval": {"status": "approved", "approved_budget_usd": budget},
        "cost_estimate": {"total_estimated_usd": sum(i["estimated_usd"] for i in items),
                          "line_items": items, "budget_verdict": "within_budget"},
        "metadata": {"paid_audio_plan": {"music": {
            "tool": tool, "unique_music_seconds": target, "seconds_per_generation": per_gen,
            "base_requests": base, "retry_requests": retry, "usd_per_request": PRICE,
        }}},
    }


def inputs(project: Path, seconds: float = 360.0) -> dict[str, Any]:
    return {"prompt": "calm piano", "custom_mode": True, "instrumental": True,
            "duration_seconds": seconds,
            "output_path": str(project / "assets" / "music" / "piece.mp3")}


def run(project: Path, packet: dict, tool: FakeMusic, evaluate=accept_all, **kw):
    tracker = approved_budget_tracker(packet, project)
    ledger = generate_music_programme(
        project_dir=project, proposal_packet=packet, tracker=tracker, tool=tool,
        inputs=kw.pop("music_inputs", None) or inputs(project), evaluate=evaluate,
        screen=kw.pop("screen", SCREEN), probe=kw.pop("probe", probe), **kw)
    return ledger, tracker


def six_minutes(tags=CALM):
    return [(359.9, tags), (359.5, tags)]


@pytest.fixture
def project(tmp_path) -> Path:
    path = tmp_path / "channel_9999__video_0001"
    path.mkdir()
    return path


# --------------------------------------------------------------------------
# A. Bounded, evidence-based screening
# --------------------------------------------------------------------------


class TestScreening:
    @pytest.mark.parametrize("text", ["smooth phrasing and controlled dynamics",
                                      "few piano voices among warm soft pads",
                                      "instrumental", "invoice", "choirmaster-free rehearsal"])
    def test_words_that_merely_contain_a_term_do_not_match(self, text):
        assert [h for h in find_prohibited_terms(text, PROHIBITED) if not h["negated"]] == []

    @pytest.mark.parametrize("text,term", [("lead vocals over piano", "vocals"),
                                           ("a Singer-songwriter ballad", "singer"),
                                           ("soft choir pads", "choir"),
                                           ("chopped vocal-chops", "vocal chops"),
                                           ("with lyrics", "lyrics")])
    def test_whole_words_do_match(self, text, term):
        hits = find_prohibited_terms(text, PROHIBITED)
        assert any(h["term"] == term and not h["negated"] for h in hits)

    @pytest.mark.parametrize("text", ["instrumental, no vocals", "without singing",
                                      "free of lyrics", "vocals-free"])
    def test_negated_mentions_are_recorded_but_do_not_fail(self, text):
        hits = find_prohibited_terms(text, PROHIBITED)
        assert hits and all(h["negated"] for h in hits)

    def test_negation_does_not_leak_across_clauses(self):
        hits = find_prohibited_terms("no drums, vocals up front", PROHIBITED)
        assert [h["negated"] for h in hits] == [False]

    @pytest.mark.parametrize("tags", INCIDENT_TAGS)
    def test_the_video_0003_tags_pass_the_bounded_screen(self, tags):
        screen = screen_music_candidate({"tags": tags, "title": "Stream to Falls"},
                                        prohibited_terms=PROHIBITED, measured_seconds=359.9,
                                        compliance_terms=["instrumental"])
        assert screen["outcome"] == "passed", screen["failures"]

    def test_a_rejection_cites_the_term_the_field_and_the_text(self):
        screen = screen_music_candidate({"tags": "ambient piano, soft choir pads"},
                                        prohibited_terms=PROHIBITED, measured_seconds=300)
        assert screen["outcome"] == "rejected"
        (failure,) = screen["failures"]
        assert failure == {**failure, "criterion": "prohibited_term", "field": "tags",
                           "term": "choir", "matched_text": "choir", "negated": False}

    def test_contradictory_metadata_is_uncertain_not_rejected(self):
        screen = screen_music_candidate({"tags": "instrumental, ethereal choir"},
                                        prohibited_terms=PROHIBITED, measured_seconds=300,
                                        compliance_terms=["instrumental"])
        assert screen["outcome"] == "uncertain"
        assert screen["uncertainties"][0]["criterion"] == "contradictory_metadata"

    def test_a_failed_probe_is_uncertain(self):
        screen = screen_music_candidate({"tags": CALM}, prohibited_terms=PROHIBITED,
                                        measured_seconds=None)
        assert screen["outcome"] == "uncertain"

    def test_measured_limits_reject_with_the_measurement(self):
        screen = screen_music_candidate(
            {"tags": CALM}, prohibited_terms=PROHIBITED, measured_seconds=300,
            measurements={"integrated_lufs": -9.0}, thresholds={"integrated_lufs": {"max": -12.0}})
        assert screen["outcome"] == "rejected"
        assert screen["failures"][0] == {"criterion": "measurement", "metric": "integrated_lufs",
                                         "value": -9.0, "limit": -12.0, "comparator": "max"}
        missing = screen_music_candidate(
            {"tags": CALM}, prohibited_terms=PROHIBITED, measured_seconds=300,
            measurements={}, thresholds={"integrated_lufs": {"max": -12.0}})
        assert missing["outcome"] == "uncertain"

    def test_passing_the_screen_is_not_acceptance(self):
        cand = {"index": 0, "tags": CALM}
        screen = screen_music_candidate(cand, prohibited_terms=PROHIBITED, measured_seconds=300)
        assert screen["outcome"] == "passed"
        creative = decide_music_candidate(
            cand, screen, {"outcome": "rejected", "criterion": "creative",
                           "evidence": "abrupt intensity jump at 2:10", "reason": "too eventful"},
            prohibited_terms=PROHIBITED)
        assert creative["outcome"] == "rejected"
        assert decide_music_candidate(cand, screen, None,
                                      prohibited_terms=PROHIBITED)["outcome"] == "uncertain"

    @pytest.mark.parametrize("evaluation", [
        {"accepted": False, "reason": "provider tags mention excluded content"},   # the incident
        {"outcome": "rejected", "criterion": "prohibited_term",
         "evidence": {"field": "tags", "term": "sing"}},                         # substring claim
        {"outcome": "rejected", "criterion": "measurement",
         "evidence": {"metric": "integrated_lufs", "value": -15.1, "limit": -12.0,
                      "comparator": "max"}},                                       # does not fail
        {"outcome": "rejected"},                                                   # no evidence
    ])
    def test_an_unsubstantiated_rejection_is_uncertain(self, evaluation):
        cand = {"index": 0, "tags": INCIDENT_TAGS[0]}
        screen = screen_music_candidate(cand, prohibited_terms=PROHIBITED, measured_seconds=359.9)
        decision = decide_music_candidate(cand, screen, evaluation, prohibited_terms=PROHIBITED)
        assert decision["outcome"] == "uncertain"
        assert decision["criterion"] == "unsubstantiated_rejection"

    def test_a_substantiated_evaluator_rejection_stands(self):
        cand = {"index": 0, "tags": CALM}
        screen = screen_music_candidate(cand, prohibited_terms=PROHIBITED, measured_seconds=300,
                                        measurements={"lra_lu": 14.2},
                                        thresholds={"lra_lu": {"max": 20}})
        decision = decide_music_candidate(
            cand, screen, {"outcome": "rejected", "criterion": "measurement",
                           "evidence": {"metric": "lra_lu", "value": 14.2, "limit": 12,
                                        "comparator": "max"}},
            prohibited_terms=PROHIBITED)
        assert decision["outcome"] == "rejected"

    def test_accepting_what_the_screen_rejects_is_contradictory(self):
        cand = {"index": 0, "tags": "lead vocals"}
        screen = screen_music_candidate(cand, prohibited_terms=PROHIBITED, measured_seconds=300)
        decision = decide_music_candidate(cand, screen, {"outcome": "accepted"},
                                          prohibited_terms=PROHIBITED)
        assert decision["outcome"] == "uncertain"

    def test_the_pipeline_names_no_channel_terms(self):
        source = (ROOT / "lib" / "relaxation_policy.py").read_text(encoding="utf-8").lower()
        for term in ("choir", "singer", "vocal chops", "river flow", "channel_0001"):
            assert term not in source


# --------------------------------------------------------------------------
# B/D. Authorisation from the approved proposal
# --------------------------------------------------------------------------


class TestApprovedLimits:
    def test_limits_come_from_the_approved_proposal(self):
        limits = approved_music_limits(proposal(), "fake_music")
        assert limits["max_requests"] == 3 and limits["base_requests"] == 2
        assert limits["target_seconds"] == 600 and limits["allocation_usd"] == 0.18

    def test_a_self_contradicting_proposal_is_refused(self):
        packet = proposal()
        packet["metadata"]["paid_audio_plan"]["music"]["retry_requests"] = 9
        with pytest.raises(MusicLimitsUnavailable):
            approved_music_limits(packet, "fake_music")

    def test_no_music_plan_means_no_music_spend(self, project):
        packet = proposal()
        del packet["metadata"]["paid_audio_plan"]["music"]
        with pytest.raises(MusicLimitsUnavailable):
            run(project, packet, FakeMusic([six_minutes()]))

    def test_a_caller_cannot_raise_the_approved_ceiling(self, project):
        tool = FakeMusic([[(100.0, CALM)]] * 9)
        packet = proposal(target=5000, base=2, retry=0)
        ledger, _ = run(project, packet, tool, max_requests=9)
        assert tool.calls == 2
        assert ledger["stop"]["reason"] == "request_ceiling_reached"
        assert ledger["stop"]["checks"]["max_requests"] == 2

    def test_the_approved_request_length_is_enforced(self, project):
        tool = FakeMusic([six_minutes()])
        ledger, _ = run(project, proposal(), tool, music_inputs=inputs(project, seconds=120))
        assert tool.calls == 0
        assert ledger["stop"]["reason"] == "request_length_differs_from_approved_plan"

    def test_every_paid_call_records_what_authorised_it(self, project):
        tool = FakeMusic([six_minutes()])
        run(project, proposal(), tool)
        (req,) = load_music_ledger(project)["requests"]
        auth = req["authorization"]
        for key in ("target_seconds", "accepted_seconds", "requests_made", "max_requests",
                    "tool_allocation_remaining_usd", "usable_budget_usd", "next_call_usd",
                    "remaining_seconds"):
            assert key in auth


# --------------------------------------------------------------------------
# E. Duration efficiency - both candidates count; targets stop generation
# --------------------------------------------------------------------------


class TestDurationEfficiency:
    def test_ten_minutes_is_one_request_when_both_candidates_pass(self, project):
        tool = FakeMusic([six_minutes()] * 3)
        ledger, tracker = run(project, proposal(target=600, base=2, retry=1), tool)
        assert tool.calls == 1 and tracker.budget_spent_usd == pytest.approx(0.06)
        assert ledger["accepted_seconds"] == pytest.approx(719.4)
        assert ledger["stop"]["reason"] == "target_met"

    def test_real_plan_numbers_for_ten_minutes_and_for_two_hours(self, project, tmp_path):
        """The proposal stage's own plan, priced by the real V6 tool (offline)."""
        music = {"tool": "suno_music", "tool_inputs": {"model": "V6", "custom_mode": True,
                                                       "instrumental": True},
                 "candidates_per_generation": 2, "accepted_per_generation": 1,
                 "retry_allowance": 0.3}
        for runtime, unique, expected_calls in ((600, 600, 1), (7200, 3300, 5)):
            plan = plan_paid_audio(target_duration_seconds=runtime,
                                   music={**music, "unique_music_seconds": unique})
            meta = plan["metadata"]["music"]
            assert meta["seconds_per_generation"] == 360.0
            packet = {"approval": {"status": "approved", "approved_budget_usd": 1.0},
                      "cost_estimate": plan["cost_estimate"],
                      "metadata": {"paid_audio_plan": plan["metadata"]}}
            where = tmp_path / f"p{runtime}"
            where.mkdir()
            tool = FakeMusic([six_minutes()] * 20, name="suno_music")
            ledger, _ = run(where, packet, tool)
            assert tool.calls == expected_calls, (runtime, ledger["stop"])
            assert ledger["stop"]["reason"] == "target_met"
            assert tool.calls < meta["base_requests"] + meta["retry_requests"]
            assert ledger["accepted_seconds"] >= unique

    def test_mixed_acceptance_counts_each_candidate_independently(self, project):
        tool = FakeMusic([[(359.9, CALM), (359.5, "ambient piano, soft choir pads")],
                          six_minutes()])
        ledger, _ = run(project, proposal(target=600, base=2, retry=1), tool)
        first, second = ledger["generations"]
        assert first["accepted_seconds"] == pytest.approx(359.9)
        assert [c["outcome"] for c in first["candidates"]] == ["accepted", "rejected"]
        assert first["candidates"][1]["evidence"][0]["term"] == "choir"
        assert tool.calls == 2 and ledger["stop"]["reason"] == "target_met"

    def test_insufficient_duration_does_not_spend_the_retry_allowance_alone(self, project):
        short = [(33.5, CALM), (17.8, CALM)]
        tool = FakeMusic([short, six_minutes()])
        packet = proposal(target=120, base=1, retry=1)
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1 and ledger["accepted_seconds"] == pytest.approx(51.3)
        assert ledger["stop"]["reason"] == "retry_requires_operator_authorization"

        record_music_review(project, request=1, reviewer="operator", note="one more is fine",
                            authorize_next_request=True)
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 2 and ledger["stop"]["reason"] == "target_met"


# --------------------------------------------------------------------------
# A/B. Rejected or uncertain results pause before another paid call
# --------------------------------------------------------------------------


class TestPauseBeforeSpending:
    def test_the_video_0003_incident_pauses_after_one_call(self, project):
        """sing-in-phrasing: formerly 2 paid calls and 0 s accepted; now 1 call, then review."""
        batches = [[(359.952, INCIDENT_TAGS[0]), (359.544, INCIDENT_TAGS[1])],
                   [(359.952, INCIDENT_TAGS[2]), (360.024, INCIDENT_TAGS[3])]]
        tool = FakeMusic(batches)
        ledger, tracker = run(project, proposal(target=120, base=1, retry=1, budget=0.55),
                              tool, evaluate=substring_screen)
        assert tool.calls == 1, "no second paid generation"
        assert tracker.budget_spent_usd == pytest.approx(0.06)
        assert ledger["stop"]["reason"] == "operator_review_required"
        assert ledger["stop"]["detail"]["reasons"] == ["uncertain_candidates"]
        outcomes = {c["criterion"] for c in ledger["generations"][0]["candidates"]}
        assert outcomes == {"unsubstantiated_rejection"}

        # The operator reviews and accepts the first candidate: target met, no new call.
        record_music_review(project, request=1, reviewer="operator", note="both are fine",
                            resolutions={0: {"outcome": "accepted", "reason": "no vocals heard"},
                                         1: {"outcome": "accepted", "reason": "no vocals heard"}})
        ledger, _ = run(project, proposal(target=120, base=1, retry=1), tool,
                        evaluate=substring_screen)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "target_met"

    def test_a_request_with_every_candidate_rejected_pauses(self, project):
        bad = [(300.0, "lead vocals, piano"), (300.0, "choir and strings")]
        tool = FakeMusic([bad, six_minutes()])
        ledger, _ = run(project, proposal(target=600, base=2, retry=1), tool)
        assert tool.calls == 1
        assert ledger["stop"]["reason"] == "operator_review_required"
        assert ledger["stop"]["detail"]["reasons"] == ["all_candidates_rejected"]

    def test_uncertain_screening_pauses(self, project):
        tool = FakeMusic([[(300.0, "instrumental, ethereal choir"), (300.0, CALM)],
                          six_minutes()])
        ledger, _ = run(project, proposal(target=900, base=3, retry=1), tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "operator_review_required"

    def test_a_failed_probe_after_a_charge_pauses(self, project):
        tool = FakeMusic([six_minutes(), six_minutes()])

        def flaky(path):
            if path.endswith("__cand1.mp3"):
                raise RuntimeError("ffprobe failed")
            return probe(path)

        ledger, _ = run(project, proposal(target=900, base=3, retry=1), tool, probe=flaky)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "operator_review_required"
        first = ledger["generations"][0]["candidates"]
        assert first[1]["outcome"] == "uncertain"

    def test_an_evaluator_that_crashes_decides_nothing(self, project):
        def broken(_):
            raise RuntimeError("evaluator bug")

        tool = FakeMusic([six_minutes(), six_minutes()])
        ledger, _ = run(project, proposal(target=900, base=3, retry=1), tool, evaluate=broken)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "operator_review_required"

    def test_an_unverifiable_charge_pauses(self, project):
        tool = FakeMusic([[(200.0, CALM)], [(200.0, CALM)]], unverified_on=1)
        ledger, _ = run(project, proposal(target=900, base=3, retry=0), tool)
        assert tool.calls == 1 and ledger["stop"]["detail"]["reasons"] == ["charge_unverified"]

    def test_the_ledger_records_intent_before_the_paid_call(self, project):
        seen = {}

        def look(_inputs):
            seen.update(load_music_ledger(project)["requests"][-1])

        run(project, proposal(), FakeMusic([six_minutes()], on_execute=look))
        assert seen["status"] == "submitting" and seen["request"] == 1


# --------------------------------------------------------------------------
# C. Resume and recovery from durable records
# --------------------------------------------------------------------------


class TestResumeAndRecovery:
    def test_resume_with_enough_music_makes_zero_calls(self, project):
        tool = FakeMusic([six_minutes()] * 3)
        run(project, proposal(), tool)
        assert tool.calls == 1
        again = FakeMusic([six_minutes()] * 3)
        ledger, _ = run(project, proposal(), again)
        assert again.calls == 0 and ledger["stop"]["reason"] == "target_met"
        assert ledger["accepted_seconds"] == pytest.approx(719.4)

    @pytest.mark.parametrize("claim", [{"requests_made": 0}, {"accepted_seconds": 0.0},
                                       {"requests_made": 0, "accepted_seconds": 0.0}])
    def test_a_caller_claim_cannot_override_the_records(self, project, claim):
        packet = proposal(target=1200, base=3, retry=1)
        tool = FakeMusic([six_minutes()] * 4)
        run(project, packet, tool, max_requests=1)
        assert tool.calls == 1
        ledger, _ = run(project, packet, tool, **claim)
        assert tool.calls == 1
        assert ledger["stop"]["reason"] == "caller_state_contradicts_records"

    def test_a_matching_caller_claim_is_fine(self, project):
        packet = proposal(target=1200, base=3, retry=1)
        tool = FakeMusic([six_minutes()] * 4)
        run(project, packet, tool, max_requests=1)
        ledger, _ = run(project, packet, tool, requests_made=1, accepted_seconds=719.4)
        assert tool.calls == 2 and ledger["stop"]["reason"] == "target_met"

    def test_an_interrupted_paid_task_is_recovered_for_free(self, project):
        packet = proposal()
        tool = FakeMusic([six_minutes()] * 3, interrupt_on=1)
        with pytest.raises(KeyboardInterrupt):
            run(project, packet, tool)
        log = json.loads((project / "cost_log.json").read_text())
        assert [e["status"] for e in log["entries"]] == ["reserved"]

        ledger, tracker = run(project, packet, tool)   # a new session resumes
        assert tool.calls == 1, "no new paid request"
        assert tool.fetches == 1
        assert ledger["stop"]["reason"] == "target_met"
        assert ledger["generations"][0]["recovered"] is True
        statuses = {e["operation"]: e["status"] for e in tracker.entries}
        assert statuses == {"music generation 1": "completed",
                            "music recovery fetch 1": "completed"}
        assert tracker.budget_spent_usd == pytest.approx(0.06)

    def test_a_timed_out_billed_task_is_recovered_on_resume(self, project):
        packet = proposal()
        tool = FakeMusic([six_minutes()] * 3, timeout_on=1)
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "provider_failure"
        ledger, tracker = run(project, packet, tool)
        assert tool.calls == 1 and tool.fetches == 1
        assert ledger["stop"]["reason"] == "target_met"
        assert tracker.budget_spent_usd == pytest.approx(0.06)

    def test_an_unknown_charge_blocks_until_the_operator_settles_it(self, project):
        packet = proposal()
        tool = FakeMusic([six_minutes()] * 3, interrupt_before_task_on=1)
        with pytest.raises(KeyboardInterrupt):
            run(project, packet, tool)
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "records_inconsistent"
        assert ledger["stop"]["detail"][0]["issue"] == "charge_outcome_unknown"

        record_music_review(project, request=1, reviewer="operator",
                            note="provider log shows no task", charge_outcome="not_charged")
        ledger, tracker = run(project, packet, tool)
        assert tool.calls == 2 and ledger["stop"]["reason"] == "target_met"
        assert tracker.budget_spent_usd == pytest.approx(0.06)

    def test_a_failed_recovery_never_turns_into_a_paid_call(self, project):
        packet = proposal()
        tool = FakeMusic([six_minutes()] * 3, interrupt_on=1, fetch_fails=True)
        with pytest.raises(KeyboardInterrupt):
            run(project, packet, tool)
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "recovery_failed"
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1

    def test_a_failed_request_is_not_retried_without_authorisation(self, project):
        class Refused(FakeMusic):
            def execute(self, inputs):
                if inputs.get("operation", "generate") == "generate" and self.calls == 0:
                    self.calls += 1
                    return ToolResult(success=False, cost_usd=0.0, error="code 429",
                                      data={"charge_status": "not_charged"})
                return super().execute(inputs)

        packet = proposal(target=600, base=1, retry=1)
        tool = Refused([None, six_minutes()])
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "provider_failure"
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1
        assert ledger["stop"]["reason"] == "retry_requires_operator_authorization"
        record_music_review(project, request=1, reviewer="operator", note="credits topped up",
                            authorize_next_request=True)
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 2 and ledger["stop"]["reason"] == "target_met"

    def test_an_unrecorded_audio_file_blocks_spending(self, project):
        music = project / "assets" / "music"
        music.mkdir(parents=True)
        (music / "piece_g01.mp3").write_text("359.9")
        tool = FakeMusic([six_minutes()])
        ledger, _ = run(project, proposal(), tool)
        assert tool.calls == 0 and ledger["stop"]["reason"] == "records_inconsistent"
        assert (music / "piece_g01.mp3").read_text() == "359.9"

    def test_a_changed_accepted_file_blocks_spending(self, project):
        tool = FakeMusic([[(200.0, CALM), (200.0, CALM)]] * 3)
        run(project, proposal(target=1200, base=3), tool, max_requests=1)
        (project / "assets" / "music" / "piece_g01.mp3").write_text("12.0")
        ledger, _ = run(project, proposal(target=1200, base=3), tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "records_inconsistent"

    def test_a_paid_call_missing_from_the_ledger_blocks_spending(self, project):
        tool = FakeMusic([[(200.0, CALM)]] * 3)
        run(project, proposal(target=1200, base=3), tool, max_requests=1)
        (project / MUSIC_LEDGER).unlink()
        (project / "assets" / "music" / "piece_g01.suno_task.json").unlink()
        ledger, _ = run(project, proposal(target=1200, base=3), tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "records_inconsistent"
        assert ledger["stop"]["detail"][0]["issue"] == "paid_request_not_in_ledger"

    def test_the_asset_manifest_must_agree_with_the_ledger(self, project):
        bad = [(300.0, "lead vocals"), (300.0, CALM)]
        tool = FakeMusic([bad, six_minutes()])
        run(project, proposal(target=300, base=2), tool)
        manifest = {"assets": [{"id": "m0", "type": "music", "source_tool": "fake_music",
                                "path": str(project / "assets" / "music" / "piece_g01.mp3")}]}
        (project / "checkpoint_assets.json").write_text(
            json.dumps({"artifacts": {"asset_manifest": manifest}}))
        state = reconcile_music_progress(project_dir=project, tool=tool, cost_entries=[],
                                         probe=probe)
        assert any(i["issue"] == "manifest_music_not_accepted_in_ledger" for i in state["issues"])

    def test_a_legacy_project_without_a_ledger_is_never_extended(self, project):
        """video_0003's shape: paid calls in cost_log, no durable music ledger."""
        packet = proposal(target=120, base=1, retry=1)
        tracker = approved_budget_tracker(packet, project)
        for n in (1, 2):
            entry = tracker.estimate("fake_music", f"music generation {n}", PRICE)
            tracker.reserve(entry)
            tracker.reconcile(entry, PRICE)
        tool = FakeMusic([six_minutes()])
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 0 and ledger["stop"]["reason"] == "records_inconsistent"
        assert [i["issue"] for i in ledger["stop"]["detail"]] == ["paid_request_not_in_ledger"] * 2


# --------------------------------------------------------------------------
# D. Spending limits, cap and pricing
# --------------------------------------------------------------------------


class TestSpendingLimits:
    def test_music_cannot_spend_the_sfx_allocation(self, project):
        # The provider charged more than estimated (no mismatch flagged): the
        # budget cap has room, but music's own approved allocation does not.
        tool = FakeMusic([[(100.0, CALM)]] * 3, charge=0.10)
        packet = proposal(target=5000, base=1, retry=1, budget=0.55, sfx_usd=0.32)
        record_music_review(project, request=1, reviewer="operator", note="continue",
                            authorize_next_request=True)
        ledger, tracker = run(project, packet, tool)
        assert tool.calls == 1
        assert ledger["stop"]["reason"] == "tool_allocation_exhausted"
        assert tracker.usable_budget_usd > PRICE, "the cap alone would have allowed it"

    def test_budget_exhaustion_stops_generation(self, project):
        tool = FakeMusic([[(100.0, CALM)]] * 3)
        ledger, _ = run(project, proposal(target=5000, base=3, retry=0, budget=0.07,
                                          sfx_usd=0), tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "budget_would_be_exceeded"

    def test_a_pricing_mismatch_stops_and_persists(self, project):
        tool = FakeMusic([[(100.0, CALM)]] * 3, mismatch_on=1)
        packet = proposal(target=5000, base=3, retry=0)
        ledger, tracker = run(project, packet, tool)
        assert tool.calls == 1 and ledger["stop"]["reason"] == "pricing_mismatch"
        ledger, _ = run(project, packet, tool)
        assert tool.calls == 1, "the mismatch blocks the next session too"
        assert ledger["stop"]["reason"] == "reservation_refused"

    def test_a_tool_outside_the_approved_plan_never_executes(self, project):
        tool = FakeMusic([six_minutes()], name="other_music")
        with pytest.raises(MusicLimitsUnavailable):
            run(project, proposal(), tool)
        assert tool.calls == 0

    def test_every_paid_call_goes_through_the_cost_tracker(self, project):
        tool = FakeMusic([[(200.0, CALM), (200.0, CALM)]] * 3)
        _, tracker = run(project, proposal(target=1200, base=3, retry=0), tool)
        paid = [e for e in tracker.entries if e["operation"].startswith("music generation")]
        assert len(paid) == tool.calls == 3


# --------------------------------------------------------------------------
# Real media: measured length comes from ffprobe on the actual file
# --------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_accepted_seconds_are_measured_by_ffprobe_on_real_files(project, tmp_path):
    fixtures = []
    for seconds in (4, 3):
        path = tmp_path / f"tone_{seconds}.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=330:duration={seconds}", str(path)],
                       capture_output=True, check=True)
        fixtures.append(path)

    class RealFiles(FakeMusic):
        def _deliver(self, out, batch, task_id, cost, keep=False):
            result = super()._deliver(out, batch, task_id, cost, keep)
            for cand, src in zip(result.data["candidates"], fixtures):
                shutil.copyfile(src, cand["path"])
            return result

    tool = RealFiles([[(0, CALM), (0, CALM)]])
    packet = proposal(target=6, base=1, retry=0, per_gen=360)
    tracker = approved_budget_tracker(packet, project)
    ledger = generate_music_programme(project_dir=project, proposal_packet=packet,
                                      tracker=tracker, tool=tool, inputs=inputs(project),
                                      evaluate=accept_all, screen=SCREEN)
    assert tool.calls == 1 and ledger["stop"]["reason"] == "target_met"
    assert ledger["accepted_seconds"] == pytest.approx(7.0, abs=0.2)


def test_no_default_screen(project):
    with pytest.raises(ValueError):
        run(project, proposal(), FakeMusic([six_minutes()]), screen={})


# --------------------------------------------------------------------------
# The Director and the assets gate teach only the authorised path
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def asset() -> str:
    text = (ROOT / "skills" / "pipelines" / "relaxation" / "asset-director.md").read_text(
        encoding="utf-8")
    return " ".join(text.split())


class TestDirectorAndGate:

    def test_the_director_forbids_substring_screening(self, asset):
        assert "Never screen with a raw substring or an unbounded regex." in asset
        assert "`sing` never matches inside `phrasing`" in asset

    def test_the_director_requires_three_evidenced_outcomes(self, asset):
        assert "`accepted`, `rejected` or `uncertain`" in asset
        assert "A **rejection must cite its criterion and evidence**" in asset

    def test_the_director_stops_before_further_spend(self, asset):
        assert "before any further paid call" in asset
        assert "record_music_review(" in asset
        assert "A retry allowance never authorises spending on its own" in asset

    def test_the_director_never_feeds_counts_to_force_a_call(self, asset):
        assert "Never pass `requests_made` or `accepted_seconds` to make it continue." in asset
        assert "`next_music_request(...)` is arithmetic only and authorises nothing." in asset
        assert "can resume from the accepted seconds and request count" not in asset

    def test_the_assets_gate_carries_the_safeguards(self):
        import yaml

        manifest = yaml.safe_load((ROOT / "pipeline_defs" / "relaxation.yaml").read_text(encoding="utf-8"))
        stage = next(s for s in manifest["stages"] if s["name"] == "assets")
        focus = " ".join(stage["review_focus"]).lower()
        for phrase in ("whole-word terms", "stop paid music until the operator records a review",
                       "caller counts never override them", "never spends the sfx allocation",
                       "recovered with the free fetch"):
            assert phrase in focus
