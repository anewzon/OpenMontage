"""Zero-spend end-to-end relaxation simulation, with every unsafe condition injected.

One production runs through every stage on the real checkpoint system -
research -> proposal -> procurement fixture -> assets -> scene plan -> edit ->
compose -> QC -> publish gate - in a temporary projects root. Paid providers
are fakes (music) or the real tool with its HTTP layer faked (SFX); footage,
music, ambience, the opening and the master are real media rendered here.

Injected and required to stop safely:

1. a false music rejection (``sing`` inside ``phrasing``)
2. a paid music request interrupted while polling, then resumed
3. direct paid-call bypass attempts against the real tools
4. SFX allocation exhaustion after a provider price rise
5. a missing licence receipt for footage the edit uses
6. an opening in the wrong pixel format and colour (video_0003's defect)
7. an attempted engine edit during production (on a frozen mirror root)
8. the camera-motion direction of a known pan

The canvas is 1280x720 in the regular suite; ``VIDQWIK_E2E_4K=1`` renders the
whole film at 3840x2160. ``VIDQWIK_E2E_REPORT=<path>`` writes the evidence.
No network: the session guard blocks it and the provider keys are fake.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib import paid_call_guard
from lib.ambience_loop import build_loop_bed, seam_report
from lib.camera_motion import analyse_clip
from lib.channel_overlay import (
    OverlayError,
    composite_overlay,
    overlay_qc,
    resolve_overlays,
    schedule_overlay,
)
from lib.channel_policy import load_channel
from lib.checkpoint import get_next_stage, read_checkpoint, write_checkpoint
from lib.delivery_qc import (
    DeliveryContract,
    compare_segments,
    conform_filter,
    delivery_filter,
    delivery_qc,
    encode_args,
    probe,
    render_opening,
)
from lib.relaxation_policy import (
    approved_budget_tracker,
    generate_music_programme,
    generate_sfx_source,
    plan_paid_audio,
    record_music_review,
    record_sfx_review,
)
from lib.relaxation_publish_gate import licence_report
from lib.relaxation_publish_gate import (
    approve_publish,
    assess_publish_readiness,
    record_licence_evidence,
    write_publish_gate,
)
from lib.stem_balance import channel_mix_from_file, measure_stem
from tests.contracts.test_relaxation_paid_music_safety import (
    INCIDENT_TAGS,
    SCREEN,
    FakeMusic,
    substring_screen,
)
from tools.audio import elevenlabs_sfx as sfx_module
from tools.audio.elevenlabs_sfx import ElevenLabsSFX
from tools.audio.suno_music import SunoMusic
from tools.cost_tracker import ApprovalRequiredError

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

ROOT = Path(__file__).resolve().parents[2]
CHANNEL = ROOT / "tests" / "fixtures" / "relaxation" / "channels" / "river_flow"
BRAND = CHANNEL / "BRAND.md"
COMPOSER = ROOT / "remotion-composer"
FOUR_K = os.environ.get("VIDQWIK_E2E_4K") == "1"
W, H = (3840, 2160) if FOUR_K else (1280, 720)
SECONDS, OPENING = 60, 3
PROJECT_ID = "channel_9999__video_0001"
SFX_SOURCES = [
    {"purpose": "principal water bed (loop)", "duration_seconds": 30, "count": 1, "loop": True},
    {"purpose": "supporting forest bed (loop)", "duration_seconds": 30, "count": 1, "loop": True},
    {"purpose": "occasional bird detail", "duration_seconds": 5, "count": 2, "loop": False},
]


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def approve(projects, stage, artifacts, **kw):
    """A gated stage: awaiting_human, then the (simulated) operator's approval."""
    write_checkpoint(projects, PROJECT_ID, stage, "awaiting_human", artifacts,
                     human_approval_required=True, **kw)
    return write_checkpoint(projects, PROJECT_ID, stage, "completed", artifacts,
                            human_approval_required=True, human_approved=True, **kw)


class RealAudioMusic(FakeMusic):
    """The fake paid music provider, delivering real audio files."""

    def __init__(self, sources, **kw):
        super().__init__([[(0, INCIDENT_TAGS[0]), (0, INCIDENT_TAGS[1])]] * 3,
                         name="suno_music", **kw)
        self.sources = sources

    def _deliver(self, out, batch, task_id, cost, keep=False):
        result = super()._deliver(out, batch, task_id, cost, keep)
        for cand, src in zip(result.data["candidates"], self.sources):
            if not (keep and Path(cand["path"]).stat().st_size > 100):
                shutil.copyfile(src, cand["path"])
        return result


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    """Run the whole production once; each test then checks one part of the evidence."""
    base = tmp_path_factory.mktemp("sim")
    report: dict = {"canvas": f"{W}x{H}", "injections": {}, "stages": {}}
    projects = base / "projects"
    project = projects / PROJECT_ID
    media = base / "media"
    media.mkdir()
    env = patch.dict(os.environ, {"SUNO_API_KEY": "fake-suno", "ELEVENLABS_API_KEY": "fake-el"})
    env.start()
    os.environ.pop(sfx_module.PRICE_ENV, None)
    import lib.events as events

    old_root = events.PROJECTS_DIR
    events.PROJECTS_DIR = projects
    http_posts: list = []
    try:
        _run(base, projects, project, media, report, http_posts)
    finally:
        events.PROJECTS_DIR = old_root
        env.stop()
        for staged in (COMPOSER / "public" / "sim").glob("*"):
            staged.unlink()
    report["http_posts_to_providers"] = len(http_posts)
    target = os.environ.get("VIDQWIK_E2E_REPORT")
    if target:
        Path(target).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return {"report": report, "projects": projects, "project": project}


def _run(base, projects, project, media, report, http_posts):
    # The channel contract is read once, at runtime, from its five files.
    channel = load_channel(CHANNEL, expected_id="channel_0001")
    report["channel"] = channel.to_metadata()
    from lib.checkpoint import init_project

    init_project(PROJECT_ID, title="Zero-spend simulation", pipeline_type="relaxation",
                 pipeline_dir=projects)
    for sub in ("licenses", "work", "output"):
        (project / sub).mkdir(exist_ok=True)

    # ---- research ------------------------------------------------------------
    brief = {
        "version": "1.0", "topic": "A forest stream on its way to the falls",
        "research_date": "2026-09-23",
        "landscape": {
            "existing_content": [{"title": f"fixture video {i}", "source": "youtube",
                                  "angle": "long-form stream", "what_it_covers": "a river"}
                                 for i in range(3)],
            "saturated_angles": ["static river loop"],
            "underserved_gaps": ["a continuous journey from stream to falls"]},
        "data_points": [{"claim": f"fixture claim {i}", "source_url": f"https://example.com/{i}",
                         "credibility": "secondary_source"} for i in range(3)],
        "audience_insights": {"common_questions": ["q1", "q2", "q3"], "misconceptions": [],
                              "knowledge_level": "general"},
        "angles_discovered": [{"name": f"angle {i}", "hook": "h", "type": "evergreen",
                               "why_now": "fixture"} for i in range(3)],
        "sources": [{"url": f"https://example.com/s{i}", "title": f"s{i}", "used_for": "fixture"}
                    for i in range(5)],
    }
    write_checkpoint(projects, PROJECT_ID, "research", "completed", {"research_brief": brief})
    report["stages"]["research"] = "completed"

    # ---- proposal: the approved plan and the delivery canvas ---------------
    plan = plan_paid_audio(
        target_duration_seconds=SECONDS,
        music={"tool": "suno_music",
               "tool_inputs": {"model": "V6", "custom_mode": True, "instrumental": True},
               "unique_music_seconds": 60, "candidates_per_generation": 2,
               "accepted_per_generation": 1, "retry_allowance": 1.0},
        sfx={"tool": "elevenlabs_sfx", "tool_inputs": {"model_id": "eleven_text_to_sound_v2"},
             "sources": SFX_SOURCES, "retry_allowance": 0.4},
        budget_cap_usd=2.0)
    packet = json.loads((ROOT / "tests/fixtures/relaxation/proposal_packet_minimal.json")
                        .read_text(encoding="utf-8"))
    packet["cost_estimate"] = plan["cost_estimate"]
    packet["approval"] = {"status": "approved", "approved_budget_usd": 2.0,
                          "user_notes": "simulated operator approval"}
    packet["metadata"] = {
        "target_duration_seconds": SECONDS, "sourcing": "licensed_manual",
        "paid_audio_plan": plan["metadata"],
        "delivery_canvas": {"width": W, "height": H, "fps": 30, "pix_fmt": "yuv420p",
                            "codec": "h264",
                            "audio": "aac 48 kHz stereo, -16 LUFS, true peak <= -1.5 dBTP"},
        "opening": {"required": True, "composition": channel.policy.opening.composition,
                    "runtime": "remotion", "bed": channel.policy.opening.bed}}
    approve(projects, "proposal", {"proposal_packet": packet})
    contract = DeliveryContract.from_proposal(packet)
    report["stages"]["proposal"] = {"approved_budget_usd": 2.0,
                                    "plan_usd": plan["cost_estimate"]["total_estimated_usd"]}

    # ---- procurement fixture: "downloaded" licensed and free footage -------
    footage = project / "assets" / "video"
    clips = {}
    for name, speed in (("RIVER1", 60), ("FALLS2", 0), ("px_901", 30)):
        path = footage / f"{name}.mp4"
        wide = W * 2
        ff("-f", "lavfi", "-i", f"testsrc2=size={wide}x{H}:rate=30:duration=20",
           "-vf", f"crop={W}:{H}:x='min(t*{speed * W / 1280:.1f}\\,{W})':y=0",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
           str(path))
        clips[name] = path
    approve(projects, "procurement", {}, metadata={"note": "fixture footage, simulated purchase"})
    report["stages"]["procurement"] = "completed (fixture)"

    # ---- camera-motion direction: a known pan right ------------------------
    motion = analyse_clip(clips["RIVER1"])
    report["injections"]["camera_motion_direction"] = {
        "clip": "RIVER1 - crop window travelling right (a camera pan right)",
        "measured": motion.summary(), "motion": motion.camera_motion,
        "direction": motion.camera_direction}

    # ---- assets: paid music (interrupted, then falsely rejected) -----------
    music_src = []
    for i, freq in enumerate((220, 262)):
        path = media / f"piano_{i}.mp3"
        ff("-f", "lavfi", "-i", f"sine=frequency={freq}:duration=65", "-f", "lavfi", "-i",
           "anoisesrc=color=pink:duration=65:amplitude=0.02", "-filter_complex",
           "[0][1]amix=inputs=2:normalize=0,volume=-12dB", "-ac", "2", "-ar", "48000", str(path))
        music_src.append(path)
    music_tool = RealAudioMusic(music_src, interrupt_on=1)
    music_inputs = {"model": "V6", "custom_mode": True, "instrumental": True, "prompt": "piano",
                    "duration_seconds": 360.0,
                    "output_path": str(project / "assets" / "music" / "piece.mp3")}

    def run_music():
        tracker = approved_budget_tracker(packet, project)
        return generate_music_programme(project_dir=project, proposal_packet=packet,
                                        tracker=tracker, tool=music_tool, inputs=music_inputs,
                                        evaluate=substring_screen, screen=SCREEN), tracker

    try:
        run_music()
        interrupted = False
    except KeyboardInterrupt:
        interrupted = True
    ledger, tracker = run_music()                     # resume: free fetch, then screening
    report["injections"]["interrupted_paid_request"] = {
        "interrupted": interrupted, "paid_calls": music_tool.calls,
        "free_fetches": music_tool.fetches, "stop": ledger["stop"]["reason"]}
    report["injections"]["false_music_rejection"] = {
        "criteria": sorted({c["criterion"] for c in ledger["generations"][0]["candidates"]}),
        "stop": ledger["stop"]["reason"], "paid_calls": music_tool.calls}
    record_music_review(project, request=1, reviewer="operator (simulated)",
                        note="listened: instrumental, no vocals",
                        resolutions={0: {"outcome": "accepted", "reason": "no vocals heard"},
                                     1: {"outcome": "rejected", "reason": "weaker take"}})
    ledger, tracker = run_music()
    report["injections"]["false_music_rejection"]["after_review"] = {
        "stop": ledger["stop"]["reason"], "paid_calls": music_tool.calls,
        "accepted_seconds": ledger["accepted_seconds"]}

    # ---- assets: direct paid-call bypass attempts ---------------------------
    def fake_post(*a, **k):
        http_posts.append(a)
        response = MagicMock(status_code=200, content=b"ID3", headers={})
        response.json.return_value = {"code": 200, "data": {"taskId": "x"}}
        return response

    with patch("requests.post", side_effect=fake_post), \
            patch("requests.get", side_effect=AssertionError("no GET expected")):
        direct_music = SunoMusic().execute({**music_inputs, "output_path":
                                            str(project / "assets" / "music" / "rogue.mp3")})
        direct_sfx = ElevenLabsSFX().execute({"prompt": "water", "duration_seconds": 30,
                                              "output_path": str(project / "assets" / "audio" /
                                                                 "rogue.mp3")})
        try:
            approved_budget_tracker(packet, project).run_tool(
                ElevenLabsSFX(), {"prompt": "water", "duration_seconds": 30,
                                  "output_path": str(project / "assets" / "audio" / "rogue2.mp3")},
                operation="SFX: extra")
            adhoc = "ALLOWED"
        except ApprovalRequiredError as exc:
            adhoc = f"refused: {exc}"
    report["injections"]["direct_paid_call_bypass"] = {
        "suno_execute": direct_music.error, "elevenlabs_execute": direct_sfx.error,
        "adhoc_run_tool": adhoc, "http_posts": len(http_posts)}

    # ---- assets: SFX through the policy, then a price rise -----------------
    sfx_audio = {}
    for name, seconds, colour in (("water", 30, "white"), ("forest", 30, "brown"),
                                  ("birds1", 5, "violet"), ("birds2", 5, "violet")):
        path = media / f"{name}.mp3"
        ff("-f", "lavfi", "-i", f"anoisesrc=color={colour}:duration={seconds}:amplitude=0.3",
           "-af", f"volume='if(lt(t,0.2)+gt(t,{seconds - 0.2}),0.63,1)':eval=frame",
           "-ac", "2", "-ar", "48000", str(path))
        sfx_audio[name] = path.read_bytes()

    def sfx_call(source, name, audio_key, **extra):
        spec = SFX_SOURCES[source - 1]
        response = MagicMock(status_code=200, content=sfx_audio[audio_key], headers={})
        with patch("requests.post", return_value=response) as post:
            out = generate_sfx_source(
                project_dir=project, proposal_packet=packet,
                tracker=approved_budget_tracker(packet, project), tool=ElevenLabsSFX(),
                inputs={"prompt": name, "model_id": "eleven_text_to_sound_v2",
                        "duration_seconds": spec["duration_seconds"], "loop": spec["loop"],
                        "output_path": str(project / "assets" / "audio" / f"{name}.mp3"),
                        **extra},
                source=source)
        return out, post.call_count

    sfx_results = [sfx_call(1, "water_bed", "water"), sfx_call(2, "forest_bed", "forest"),
                   sfx_call(3, "birds_a", "birds1")]
    os.environ[sfx_module.PRICE_ENV] = "0.36"          # the provider triples its price
    sfx_results.append(sfx_call(3, "birds_b", "birds2"))
    record_sfx_review(project, reviewer="operator (simulated)", note="retry the water bed",
                      authorize_retry_source=1)
    exhausted, posts = sfx_call(1, "water_bed_v2", "water")
    tracker = approved_budget_tracker(packet, project)
    report["injections"]["sfx_allocation_exhaustion"] = {
        "generated": [r["operation"] for r, _ in sfx_results if r["generated"]],
        "extra_call": exhausted["reason"], "extra_call_http_posts": posts,
        "sfx_spent_usd": tracker.tool_spent_usd("elevenlabs_sfx"),
        "sfx_allocation_usd": tracker.allocations["elevenlabs_sfx"],
        "music_spent_usd": tracker.tool_spent_usd("suno_music"),
        "cap_room_usd": round(tracker.usable_budget_usd, 4)}
    os.environ.pop(sfx_module.PRICE_ENV, None)

    # ---- asset manifest ------------------------------------------------------
    candidate = project / "assets" / "music" / "piece_g01.mp3"
    assets = [
        {"id": "RIVER1", "type": "video", "path": str(clips["RIVER1"]), "scene_id": "S1",
         "source_tool": "licensed_manual:envato", "provider": "envato"},
        {"id": "FALLS2", "type": "video", "path": str(clips["FALLS2"]), "scene_id": "S2",
         "source_tool": "licensed_manual:envato", "provider": "envato"},
        {"id": "px_901", "type": "video", "path": str(clips["px_901"]), "scene_id": "S3",
         "source_tool": "pexels_video", "license": "Pexels License",
         "original_url": "https://www.pexels.com/video/901/"},
        {"id": "music_g01_c0", "type": "music", "path": str(candidate), "scene_id": "all",
         "source_tool": "suno_music"},
    ] + [{"id": f"sfx_{n}", "type": "sfx", "path": str(project / "assets" / "audio" / f"{n}.mp3"),
          "scene_id": "all", "source_tool": "elevenlabs_sfx"}
         for n in ("water_bed", "forest_bed", "birds_a", "birds_b")]
    manifest = {"version": "1.0", "assets": assets, "metadata": {"licence_evidence": {}}}
    (project / "licenses" / "RIVER1_certificate.pdf").write_bytes(b"%PDF-1.4 fixture licence")
    record_licence_evidence(project, manifest, "RIVER1", "licenses/RIVER1_certificate.pdf",
                            licence="Envato Elements (fixture)", item="RIVER1",
                            verified_by="operator (simulated)")
    write_checkpoint(projects, PROJECT_ID, "assets", "completed", {"asset_manifest": manifest})
    report["stages"]["assets"] = "completed"

    # ---- scene plan ------------------------------------------------------------
    scenes = {"version": "1.0", "scenes": [
        {"id": f"S{i + 1}", "type": "broll", "description": f"movement {i + 1}",
         "start_seconds": float(OPENING + i * 19), "end_seconds": float(OPENING + (i + 1) * 19)}
        for i in range(3)]}
    approve(projects, "scene_plan", {"scene_plan": scenes})

    # ---- edit: beds with checked seams, the channel's mix --------------------
    work = project / "work"
    audio_dir = project / "assets" / "audio"
    beds = {}
    for role, name in (("A2-water", "water_bed"), ("A4-forest", "forest_bed")):
        bed = build_loop_bed(audio_dir / f"{name}.mp3", work / "stems" / f"{role}_raw.wav",
                             SECONDS)
        beds[role] = {**bed, "seams": seam_report(bed["path"], bed["seam_seconds"])}
    mix = channel_mix_from_file(BRAND)
    stems = work / "stems"
    ff("-i", beds["A2-water"]["path"], "-af", mix.principal_treatment_af,
       str(stems / "A2-water.wav"))
    ff("-i", beds["A4-forest"]["path"], str(stems / "A4-forest.wav"))
    ff("-i", str(audio_dir / "birds_a.mp3"), "-i", str(audio_dir / "birds_b.mp3"),
       "-filter_complex", "[0]adelay=12000|12000[a];[1]adelay=38000|38000[b];"
       "[a][b]amix=inputs=2:normalize=0,apad=whole_dur=60", "-t", "60",
       str(stems / "A3-birds.wav"))
    ff("-i", str(candidate), "-af", "atrim=0:60,afade=t=in:d=1.5,afade=t=out:st=54:d=6",
       str(stems / "A1-music.wav"))
    built = {r: measure_stem(stems / f"{r}.wav").integrated_lufs
             for r in ("A1-music", "A2-water", "A3-birds", "A4-forest")}
    plan_mix = mix.solve(built)
    remeasured = {}
    for role, gain in plan_mix.gains_db.items():
        ff("-i", str(stems / f"{role}.wav"), "-af", f"volume={gain}dB",
           str(stems / f"{role}_gain.wav"))
        remeasured[role] = measure_stem(stems / f"{role}_gain.wav").integrated_lufs
    verification = plan_mix.verify(remeasured, tolerance_lu=0.5)
    ff(*[a for r in ("A1-music", "A2-water", "A3-birds", "A4-forest")
         for a in ("-i", str(stems / f"{r}_gain.wav"))],
       "-filter_complex", "amix=inputs=4:normalize=0,alimiter=limit=0.6:level=false",
       "-ar", "48000", "-ac", "2", str(work / "mix_raw.wav"))
    level = measure_stem(work / "mix_raw.wav").integrated_lufs
    ff("-i", str(work / "mix_raw.wav"), "-af",
       f"volume={contract.integrated_lufs - level}dB,alimiter=limit=0.7:level=false",
       str(work / "mix.wav"))
    edit = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "cuts": [{"id": "c1", "source": "RIVER1", "in_seconds": 0, "out_seconds": 19},
                 {"id": "c2", "source": "FALLS2", "in_seconds": 0, "out_seconds": 19},
                 {"id": "c3", "source": "px_901", "in_seconds": 0, "out_seconds": 19}],
        "metadata": {
            "opening": {"required": True, "bed_asset_id": "RIVER1",
                        "rendered": str(work / "opening.mp4")},
            "audio_layers": [
                {"role": "A1-music", "asset_id": "music_g01_c0"},
                {"role": "A2-water", "beds": [{"asset_id": "sfx_water_bed"}]},
                {"role": "A3-birds", "beds": [{"asset_id": "sfx_birds_a"},
                                              {"asset_id": "sfx_birds_b"}]},
                {"role": "A4-forest", "beds": [{"asset_id": "sfx_forest_bed"}]}],
            "mix_balance": mix.record(plan_mix, verification),
            "loop_seams": {r: b["seams"] for r, b in beds.items()},
            # The channel's declared overlays, resolved and scheduled from THIS
            # episode. This channel declares none, so none is recorded.
            "overlays": [
                schedule_overlay(r, runtime_seconds=SECONDS, opening_seconds=OPENING,
                                 slots=scenes["scenes"], frame=(W, H))
                for r in resolve_overlays(channel.policy.overlays, channel.root, frame=(W, H))],
        }}
    write_checkpoint(projects, PROJECT_ID, "edit", "awaiting_human", {"edit_decisions": edit},
                     human_approval_required=True)
    report["stages"]["edit_mix"] = {
        "achieved_offsets_db": verification.achieved_offsets_db,
        "achieved_group_offsets_db": verification.achieved_group_offsets_db,
        "channel_band_failures": mix.check(verification),
        "seams_passed": {r: b["seams"]["passed"] for r, b in beds.items()},
        "worst_seam_db": {r: b["seams"]["worst_seam_deviation_db"] for r, b in beds.items()}}

    # ---- injection: an engine edit attempted during production --------------
    mirror = base / "VidQwik AI" / "OpenMontage"
    (mirror / "lib").mkdir(parents=True)
    shutil.copyfile(ROOT / "lib" / "stem_balance.py", mirror / "lib" / "stem_balance.py")
    (base / "VidQwik AI" / "Channels" / "channel_9999").mkdir(parents=True)
    shutil.copyfile(BRAND, base / "VidQwik AI" / "Channels" / "channel_9999" / "BRAND.md")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "."], ["commit", "-qm", "engine"]):
        subprocess.run(["git", *args], cwd=mirror, capture_output=True, check=True)
    from lib import production_mode

    production_mode.enable(mirror, operator="operator (simulated)")
    engine = mirror / "lib" / "stem_balance.py"
    before = engine.read_bytes()
    try:
        engine.write_text("WATER_BAND_DB = (-8.0, -6.0)  # 'quick fix'\n")
        edit_attempt = "ALLOWED"
    except PermissionError as exc:
        edit_attempt = f"refused: {type(exc).__name__}"
    git_attempt = subprocess.run(["git", "commit", "--allow-empty", "-m", "hotfix"], cwd=mirror,
                                 capture_output=True, text=True)
    defect = production_mode.record_platform_defect(
        project, stage="edit", summary="an agent tried to patch lib/stem_balance.py mid-production",
        evidence=f"write: {edit_attempt}; git: {git_attempt.stderr.strip()[-80:]}")
    edit_checkpoint = read_checkpoint(projects, PROJECT_ID, "edit")
    report["injections"]["production_source_edit"] = {
        "write": edit_attempt, "git_commit_rc": git_attempt.returncode,
        "engine_unchanged": engine.read_bytes() == before,
        "checkpoint": edit_checkpoint["status"],
        "artifacts_kept": "edit_decisions" in edit_checkpoint["artifacts"],
        "report": Path(defect["report"]).name}
    for path in mirror.parent.rglob("*"):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    approve(projects, "edit", {"edit_decisions": edit})     # resumed after the "fix"
    report["stages"]["edit"] = "completed after the defect stop"

    # ---- compose: body, a wrong opening, the contract opening, the master ---
    body = work / "body.mp4"
    per = (SECONDS - OPENING) / 3
    parts = []
    for i, name in enumerate(("RIVER1", "FALLS2", "px_901")):
        part = work / f"body_{i}.mp4"
        ff("-ss", "0", "-t", f"{per}", "-i", str(clips[name]), "-vf",
           conform_filter(probe(clips[name]), contract), *encode_args(contract, audio=False),
           str(part))
        parts.append(part)
    listing = work / "body.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(body))

    wrong = work / "opening_wrong.mp4"
    ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30:duration={OPENING}",
       "-vf", "scale=out_range=full:out_color_matrix=bt601,format=yuvj420p", "-c:v", "libx264",
       "-pix_fmt", "yuvj420p", "-color_range", "pc", "-colorspace", "smpte170m",
       "-color_primaries", "bt470bg", "-color_trc", "smpte170m", str(wrong))
    wrong_problems = compare_segments(wrong, body, contract)

    opening = work / "opening.mp4"
    remotion = (COMPOSER / "node_modules" / ".bin").is_dir()
    if remotion:
        staged = COMPOSER / "public" / "sim" / "bed.mp4"
        staged.parent.mkdir(parents=True, exist_ok=True)
        ff("-ss", "2", "-t", str(OPENING), "-i", str(clips["RIVER1"]), "-an", "-c:v", "libx264",
           "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(staged))
        rendered = render_opening(
            composer_dir=COMPOSER, composition=channel.policy.opening.composition, output=opening,
            contract=contract,
            props={"videoSrc": "sim/bed.mp4", "brandSignature": "Test Channel",
                   "welcomeMessage": "FOLLOW THE WATER", "episodeLine": "a simulated stream",
                   "durationSeconds": OPENING})
        opening_source = {"runtime": "remotion", "raw_format": rendered["raw_format"]}
    else:
        ff("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30:duration={OPENING}",
           "-vf", delivery_filter(contract), *encode_args(contract, audio=False), str(opening))
        opening_source = {"runtime": "ffmpeg (remotion-composer dependencies not installed)"}
    good_problems = compare_segments(opening, body, contract)

    def master(opening_file, name):
        joined = work / f"{name}_picture.mp4"
        lst = work / f"{name}.txt"
        lst.write_text(f"file '{Path(opening_file).as_posix()}'\nfile '{body.as_posix()}'\n")
        ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined))
        out = project / "output" / "final.mp4" if name == "final" else work / f"{name}.mp4"
        ff("-i", str(joined), "-i", str(work / "mix.wav"), "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", str(out))
        return out

    wrong_master = master(wrong, "wrong")
    wrong_qc = delivery_qc(wrong_master, contract, opening=wrong, body=body)
    final = master(opening, "final")
    qc = delivery_qc(final, contract, opening=opening, body=body)
    qc["body"] = str(body)
    report["injections"]["wrong_opening_format"] = {
        "compare_segments": wrong_problems, "qc_passed": wrong_qc["passed"],
        "qc_blockers": wrong_qc["blockers"]}
    report["stages"]["compose"] = {"opening": opening_source, "opening_vs_body": good_problems,
                                   "qc_passed": qc["passed"], "qc_blockers": qc["blockers"],
                                   "measurements": qc["measurements"]}
    render = {"version": "1.0",
              "outputs": [{"path": str(final), "format": "mp4", "resolution": f"{W}x{H}",
                           "duration_seconds": qc["measurements"]["duration_seconds"]}],
              "metadata": {"qc": {"technical": qc}}}
    write_checkpoint(projects, PROJECT_ID, "compose", "completed", {"render_report": render})

    # ---- channel overlays: none for this channel; the mechanism proven beside it ----
    river_overlay_qc = overlay_qc(final, edit["metadata"]["overlays"], contract,
                                  expected_ids=[o.id for o in channel.policy.overlays])
    overlay_root = base / "VidQwik AI" / "Channels" / "channel_9998"
    shutil.copytree(CHANNEL.parent / "unseen_america", overlay_root)
    brand_path = overlay_root / "BRAND.md"
    brand_text = brand_path.read_text(encoding="utf-8").replace(
        "channel_id: channel_0002", "channel_id: channel_9998").replace(
        "```\n\n## Channel mix settings",
        "overlays:\n  - id: badge\n    enabled: true\n"
        "    asset: brand_assets/overlays/badge.mov\n    purpose: subscribe CTA\n"
        "    usage: once_per_video\n    placement: bottom_right\n    scale: 0.22\n"
        "```\n\n## Channel mix settings")
    brand_path.write_text(brand_text, encoding="utf-8")
    badge = overlay_root / "brand_assets" / "overlays" / "badge.mov"
    badge.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", "color=c=red:s=240x90:r=30:d=3,format=rgba,"
       "pad=320:180:40:45:color=black@0.0", "-f", "lavfi", "-i",
       "sine=frequency=1000:duration=3", "-c:v", "prores_ks", "-profile:v", "4444",
       "-pix_fmt", "yuva444p10le", "-c:a", "pcm_s16le", str(badge))
    other = load_channel(overlay_root, expected_id="channel_9998")
    resolved = resolve_overlays(other.policy.overlays, other.root, frame=(W, H))
    overlay_record = schedule_overlay(resolved[0], runtime_seconds=SECONDS,
                                      opening_seconds=OPENING, slots=[
        {**sc, "shot_scale": "wide", "subject_motion": "gentle"} for sc in scenes["scenes"]],
        frame=(W, H))
    overlay_body = work / "body_with_overlay.mp4"
    overlay_record = composite_overlay(body, overlay_body, overlay_record, contract)
    lst = work / "overlay_final.txt"
    lst.write_text(f"file '{opening.as_posix()}'\nfile '{overlay_body.as_posix()}'\n")
    joined = work / "overlay_final_picture.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined))
    overlay_final = work / "overlay_final.mp4"
    ff("-i", str(joined), "-i", str(work / "mix.wav"), "-map", "0:v", "-map", "1:a",
       "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
       "-movflags", "+faststart", str(overlay_final))
    overlay_delivery = delivery_qc(overlay_final, contract, opening=opening, body=overlay_body)
    other_qc = overlay_qc(overlay_final, [overlay_record], contract, expected_ids=["badge"])
    overlay_edit = {"cuts": [], "metadata": {"overlays": [overlay_record]}}
    unproven = licence_report(project, {"assets": []}, overlay_edit, channel_root=other.root)
    (other.root / "brand_assets" / "PROVENANCE.md").write_text(
        "# Provenance\n\n```channel-provenance\noverlays/badge.mov:\n"
        "  licence: made in-house (simulated)\n  source: fixture\n"
        "  verified_by: operator (simulated)\n  date: 2026-09-23\n```\n", encoding="utf-8")
    proven = licence_report(project, {"assets": []}, overlay_edit, channel_root=other.root)
    badge.unlink()
    try:
        resolve_overlays(other.policy.overlays, other.root, frame=(W, H))
        missing_asset = "ALLOWED"
    except OverlayError as exc:
        missing_asset = f"stopped: {exc}"[:120]
    report["stages"]["overlays"] = {
        "this_channel_declared": len(channel.policy.overlays),
        "this_channel_records": edit["metadata"]["overlays"],
        "this_channel_qc_passed": river_overlay_qc["passed"],
        "other_channel": {
            "declared": [o.id for o in other.policy.overlays],
            "media": resolved[0].media.to_metadata(),
            "record": {k: overlay_record[k] for k in ("id", "start_seconds", "end_seconds",
                                                       "region", "slot_id", "audio",
                                                       "rationale")},
            "evidence": overlay_record["evidence"],
            "overlay_qc_passed": other_qc["passed"], "overlay_qc_blockers": other_qc["blockers"],
            "delivery_qc_passed": overlay_delivery["passed"],
            "delivery_qc_blockers": overlay_delivery["blockers"],
            "provenance_missing_blockers": unproven["blockers"],
            "provenance_verified": proven["verified"],
            "missing_asset": missing_asset}}

    # ---- publish gate: first with a receipt missing, then complete ------------
    gate_kwargs = dict(proposal_packet=packet, asset_manifest=manifest, edit_decisions=edit,
                       render_report=render, channel_brand=BRAND)
    blocked = assess_publish_readiness(project, **gate_kwargs)
    write_publish_gate(projects, PROJECT_ID, blocked)
    blocked_checkpoint = read_checkpoint(projects, PROJECT_ID, "publish")
    report["injections"]["missing_licence"] = {
        "ready": blocked["ready"], "blockers": blocked["blockers"],
        "checkpoint": blocked_checkpoint["status"],
        "unused_alternates": blocked["licence"]["unused_alternates"]}
    (project / "licenses" / "FALLS2_certificate.pdf").write_bytes(b"%PDF-1.4 fixture licence 2")
    record_licence_evidence(project, manifest, "FALLS2", "licenses/FALLS2_certificate.pdf",
                            licence="Envato Elements (fixture)", item="FALLS2",
                            verified_by="operator (simulated)")
    write_checkpoint(projects, PROJECT_ID, "assets", "completed", {"asset_manifest": manifest})
    ready = assess_publish_readiness(project, **gate_kwargs)
    write_publish_gate(projects, PROJECT_ID, ready)
    approve_publish(projects, PROJECT_ID, operator_note="simulated operator approval",
                    **gate_kwargs)
    tracker = approved_budget_tracker(packet, project)
    report["stages"]["publish"] = {
        "ready": ready["ready"], "blockers": ready["blockers"],
        "checkpoint": read_checkpoint(projects, PROJECT_ID, "publish")["status"],
        "next_stage": get_next_stage(projects, PROJECT_ID, "relaxation")}
    report["simulated_provider_charges_usd"] = round(tracker.budget_spent_usd, 4)
    report["real_spend_usd"] = 0.0


# ------------------------------------------------------------------------------
# The evidence
# ------------------------------------------------------------------------------


def test_every_stage_completed_and_nothing_remains(sim):
    stages = sim["report"]["stages"]
    assert stages["publish"]["checkpoint"] == "completed"
    assert stages["publish"]["next_stage"] is None
    assert stages["publish"]["ready"] and stages["publish"]["blockers"] == []


def test_the_false_music_rejection_paused_before_a_second_paid_call(sim):
    rejection = sim["report"]["injections"]["false_music_rejection"]
    assert rejection["criteria"] == ["unsubstantiated_rejection"]
    assert rejection["stop"] == "operator_review_required" and rejection["paid_calls"] == 1
    after = rejection["after_review"]
    assert after["stop"] == "target_met" and after["paid_calls"] == 1
    assert after["accepted_seconds"] >= 60


def test_the_interrupted_paid_request_was_recovered_for_free(sim):
    interrupted = sim["report"]["injections"]["interrupted_paid_request"]
    assert interrupted["interrupted"] and interrupted["paid_calls"] == 1
    assert interrupted["free_fetches"] == 1


def test_direct_paid_calls_never_reached_a_provider(sim):
    bypass = sim["report"]["injections"]["direct_paid_call_bypass"]
    assert "refused" in bypass["suno_execute"] and "refused" in bypass["elevenlabs_execute"]
    assert bypass["adhoc_run_tool"].startswith("refused")
    assert bypass["http_posts"] == 0 and sim["report"]["http_posts_to_providers"] == 0


def test_sfx_stopped_at_its_own_allocation(sim):
    sfx = sim["report"]["injections"]["sfx_allocation_exhaustion"]
    assert len(sfx["generated"]) == 4
    assert sfx["extra_call"] == "tool_allocation_exhausted" and sfx["extra_call_http_posts"] == 0
    assert sfx["sfx_spent_usd"] <= sfx["sfx_allocation_usd"]
    assert sfx["cap_room_usd"] > 0.5, "the overall cap still had room"


def test_the_missing_licence_blocked_publishing(sim):
    missing = sim["report"]["injections"]["missing_licence"]
    assert missing["ready"] is False and missing["checkpoint"] == "failed"
    assert missing["blockers"] and all(b.startswith("licence:") for b in missing["blockers"])
    assert any("FALLS2" in b for b in missing["blockers"])


def test_the_wrong_opening_was_blocked_and_the_contract_opening_matched(sim):
    wrong = sim["report"]["injections"]["wrong_opening_format"]
    assert wrong["compare_segments"] and wrong["qc_passed"] is False
    compose = sim["report"]["stages"]["compose"]
    assert compose["opening_vs_body"] == [] and compose["qc_passed"], compose["qc_blockers"]


def test_the_engine_edit_was_refused_and_the_project_checkpointed(sim):
    edit = sim["report"]["injections"]["production_source_edit"]
    assert edit["write"].startswith("refused") and edit["git_commit_rc"] != 0
    assert edit["engine_unchanged"] and edit["checkpoint"] == "failed" and edit["artifacts_kept"]


def test_the_camera_pan_reads_in_its_true_direction(sim):
    motion = sim["report"]["injections"]["camera_motion_direction"]
    assert (motion["motion"], motion["direction"]) in {("pan", "right"), ("tracking", "right")}, \
        motion["measured"]


def test_the_mix_is_the_channel_block_and_the_seams_are_clean(sim):
    mix = sim["report"]["stages"]["edit_mix"]
    assert mix["channel_band_failures"] == []
    assert mix["achieved_offsets_db"]["A2-water"] == pytest.approx(-29.0, abs=0.5)
    assert all(mix["seams_passed"].values())


def test_this_channel_has_no_overlay_and_the_mechanism_works_for_one_that_does(sim):
    ov = sim["report"]["stages"]["overlays"]
    assert ov["this_channel_declared"] == 0 and ov["this_channel_records"] == []
    assert ov["this_channel_qc_passed"]
    other = ov["other_channel"]
    assert other["declared"] == ["badge"]
    assert other["media"]["has_alpha_plane"] and other["media"]["alpha_varies"]
    assert other["media"]["audio_streams"] == 1 and other["record"]["audio"] == "excluded"
    assert other["evidence"]["output_audio_streams"] == 0   # the body carries no audio
    assert other["evidence"]["frame_diff_inside_window"] > \
        3 * other["evidence"]["frame_diff_outside_window"]
    assert OPENING < other["record"]["start_seconds"] and \
        other["record"]["end_seconds"] <= SECONDS - 5
    assert other["overlay_qc_passed"], other["overlay_qc_blockers"]
    assert other["delivery_qc_passed"], other["delivery_qc_blockers"]
    assert other["provenance_missing_blockers"] and \
        other["provenance_missing_blockers"][0].startswith("overlay:badge")
    assert "overlay:badge" in other["provenance_verified"]
    assert other["missing_asset"].startswith("stopped")


def test_no_real_money_was_spent(sim):
    assert sim["report"]["real_spend_usd"] == 0.0
    assert sim["report"]["http_posts_to_providers"] == 0
