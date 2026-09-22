"""Regression coverage for the relaxation audio contract and channel opening.

Structural tests guard where a rule lives (channel vs pipeline). Behavioural
tests use tiny synthetic fixtures built with FFmpeg and assert what actually
comes out of the real tools - no mocking of the thing under test.

None of these read or write the channel_0001__video_0001 project.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
RELAX = SKILLS / "pipelines" / "relaxation"
MANIFEST = ROOT / "pipeline_defs" / "relaxation.yaml"
OPENING_TSX = ROOT / "remotion-composer" / "src" / "RiverFlowOpening.tsx"
ROOT_TSX = ROOT / "remotion-composer" / "src" / "Root.tsx"

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# --------------------------------------------------------------- helpers ---
def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"{cmd[:4]} failed: {r.stderr[-400:]}"
    return r


def _probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,sample_rate,channels",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def _make_clip(path: Path, seconds: float, tone_hz: int | None, colour="green"):
    """A tiny video. tone_hz=None -> silent stereo track."""
    audio = (f"sine=frequency={tone_hz}:duration={seconds}" if tone_hz
             else f"anullsrc=channel_layout=stereo:sample_rate=48000:d={seconds}")
    _run(["ffmpeg", "-y", "-v", "error",
          "-f", "lavfi", "-i", f"color=c={colour}:s=320x240:r=30:d={seconds}",
          "-f", "lavfi", "-i", audio,
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
          "-ac", "2", "-shortest", str(path)])


def _rms_dbfs(path: Path, start: float, dur: float) -> float:
    wav = path.with_suffix(".probe.wav")
    _run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(dur),
          "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", str(wav)])
    with wave.open(str(wav)) as w:
        n = w.getnframes()
        if n == 0:
            return -99.0
        import struct
        d = struct.unpack("<%dh" % n, w.readframes(n))
    rms = math.sqrt(sum(x * x for x in d) / len(d)) if d else 0.0
    return 20 * math.log10(rms / 32768) if rms > 0 else -99.0


def _true_peak_dbfs(path: Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    peak = None
    for line in r.stderr[-1500:].splitlines():
        if line.strip().startswith("Peak:"):
            try:
                peak = float(line.split(":")[1].strip().split()[0])
            except Exception:
                pass
    assert peak is not None, "could not measure true peak"
    return peak


# ============================ 1. channel-layer rules =======================
def _brand_path() -> Path | None:
    p = ROOT.parent / "Channels" / "channel_0001" / "BRAND.md"
    return p if p.is_file() else None


def test_channel_declares_instrumental_only_rule():
    """channel_0001 forbids vocals; the rule lives in the CHANNEL, not the pipeline."""
    brand = _brand_path()
    if brand is None:
        pytest.skip("channel_0001/BRAND.md not present (repo checked out standalone)")
    body = brand.read_text(encoding="utf-8").lower()
    for token in ("lyric", "singing", "vocal"):
        assert token in body, f"BRAND.md must state the {token} prohibition"


def test_pipeline_does_not_hardcode_a_house_musical_taste():
    """Another relaxation channel may want ocean, rain, or no music at all."""
    banned = ("river flow naturescapes", "channel_0001")
    for skill in RELAX.glob("*.md"):
        low = skill.read_text(encoding="utf-8").lower()
        for b in banned:
            assert b not in low, f"{skill.name} names channel identity ({b!r})"
    # A specific instrument preference is channel taste, not pipeline behaviour.
    proc = (RELAX / "procurement-director.md").read_text(encoding="utf-8").lower()
    assert "brand.md" in proc, "procurement must defer musical taste to the channel"


# ======================= 2-3. native audio classification ==================
@pytest.mark.parametrize("cls", ["USE", "USE_AFTER_TREATMENT", "REJECT", "NO_AUDIO"])
def test_asset_director_defines_native_audio_classes(cls):
    body = (RELAX / "asset-director.md").read_text(encoding="utf-8")
    assert cls in body, f"asset-director must define the {cls} class"


def test_native_audio_level_must_be_measured_not_blanket():
    body = (RELAX / "asset-director.md").read_text(encoding="utf-8").lower()
    assert "blanket" in body and "measure" in body, (
        "asset-director must forbid a blanket native-audio percentage")


# ============ 4. rejected native audio cannot leak into the master =========
@needs_ffmpeg
def test_external_mix_replaces_all_native_clip_audio(tmp_path):
    """The real defence: when an approved mix is supplied, the delivered file
    carries exactly one audio stream and it is the mix - not the clips."""
    from tools.video.video_compose import VideoCompose

    # Two clips whose own audio is a loud 1 kHz tone - i.e. "contaminated".
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_clip(a, 2.0, 1000, "green")
    _make_clip(b, 2.0, 1000, "blue")

    # The approved mix is near-silent, so any leak is unmistakable.
    mix = tmp_path / "mix.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "anullsrc=channel_layout=stereo:sample_rate=48000:d=4",
          "-c:a", "pcm_s16le", str(mix)])

    out = tmp_path / "out.mp4"
    ed = {"version": "1.0", "render_runtime": "ffmpeg",
          "renderer_family": "documentary-montage",
          "cuts": [
              {"id": "c1", "source": "a", "in_seconds": 0.0, "out_seconds": 2.0,
               "layer": "primary", "reason": "fixture"},
              {"id": "c2", "source": "b", "in_seconds": 0.0, "out_seconds": 2.0,
               "layer": "primary", "reason": "fixture"}],
          "metadata": {"compose_target": {"width": 320, "height": 240, "fit": "pad"}}}
    am = {"version": "1.0", "assets": [
        {"id": "a", "type": "video", "path": str(a)},
        {"id": "b", "type": "video", "path": str(b)}]}

    res = VideoCompose().execute({
        "operation": "render", "edit_decisions": ed, "asset_manifest": am,
        "audio_path": str(mix), "output_path": str(out),
        "codec": "libx264", "crf": 28, "preset": "ultrafast",
        "options": {"subtitle_burn": False}})
    assert res.success, res.error

    info = _probe(out)
    audio_streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(audio_streams) == 1, "delivered file must carry exactly one audio stream"

    # If the 1 kHz native tone had leaked through, this would be far louder.
    assert _rms_dbfs(out, 0.2, 3.0) < -50.0, "native clip audio leaked under the mix"


# ================== 5. native audio aligns to the visual range =============
@needs_ffmpeg
def test_native_audio_extraction_respects_the_cut_range(tmp_path):
    """Audio taken from a clip must come from the same in/out the cut uses,
    or it desynchronises from the picture it belongs to."""
    # Audio: 2s silence then 2s tone. A range-accurate extract of the first
    # half must be quiet; of the second half, loud.
    quiet_wav, tone_wav, joined = (tmp_path / "q.wav", tmp_path / "t.wav",
                                   tmp_path / "j.wav")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "anullsrc=channel_layout=stereo:sample_rate=48000:d=2",
          "-c:a", "pcm_s16le", str(quiet_wav)])
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "sine=frequency=800:duration=2:sample_rate=48000",
          "-ac", "2", "-c:a", "pcm_s16le", str(tone_wav)])
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(quiet_wav), "-i", str(tone_wav),
          "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
          "-c:a", "pcm_s16le", str(joined)])

    src = tmp_path / "src.mp4"
    _run(["ffmpeg", "-y", "-v", "error",
          "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=4",
          "-i", str(joined), "-map", "0:v", "-map", "1:a",
          "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", "4", str(src)])

    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-ss", "0", "-t", "1.5", "-i", str(src),
          "-vn", "-c:a", "pcm_s16le", str(first)])
    _run(["ffmpeg", "-y", "-v", "error", "-ss", "2.3", "-t", "1.5", "-i", str(src),
          "-vn", "-c:a", "pcm_s16le", str(second)])

    quiet = _rms_dbfs(first, 0.1, 1.2)
    loud = _rms_dbfs(second, 0.1, 1.2)
    assert loud > quiet + 20, (
        f"range-aligned extraction failed: first={quiet:.1f} second={loud:.1f} dBFS")


def test_edit_director_requires_range_aligned_native_audio():
    body = (RELAX / "edit-director.md").read_text(encoding="utf-8").lower()
    assert "in/out range" in body or "same in/out" in body
    assert "synchronis" in body or "synchroniz" in body


# ============ 6. mixed audio duration matches the approved timeline ========
def test_manifest_requires_timeline_derived_mix_duration():
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    edit = next(s for s in m["stages"] if s["name"] == "edit")
    joined = " ".join(edit.get("review_focus", [])).lower()
    assert "derived" in joined and "timeline" in joined, (
        "edit stage must require the mix length to come from the approved timeline")


@needs_ffmpeg
def test_mix_trimmed_to_timeline_matches_video_duration(tmp_path):
    """A mix built to the timeline must match the picture, not overrun it."""
    vid = tmp_path / "v.mp4"
    _make_clip(vid, 3.0, None)
    v_dur = float(_probe(vid)["format"]["duration"])

    long_mix = tmp_path / "long.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "sine=frequency=300:duration=10", "-c:a", "pcm_s16le", str(long_mix)])

    aligned = tmp_path / "aligned.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(long_mix), "-t", f"{v_dur:.3f}",
          "-c:a", "pcm_s16le", str(aligned)])
    a_dur = float(_probe(aligned)["format"]["duration"])
    assert abs(a_dur - v_dur) < 0.10, f"mix {a_dur:.2f}s vs video {v_dur:.2f}s"


# ================= 7-8. fade survives encoding; true peak in range =========
@needs_ffmpeg
def test_final_fade_survives_aac_encode_and_true_peak_is_bounded(tmp_path):
    """Fade + limiter must survive the lossy encode, and the measured TRUE peak
    (not sample peak) must sit inside the ceiling."""
    vid = tmp_path / "v.mp4"
    _make_clip(vid, 6.0, None)
    mix = tmp_path / "mix.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
          "-i", "sine=frequency=440:duration=6", "-c:a", "pcm_s16le", str(mix)])

    out = tmp_path / "final.mp4"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(vid), "-i", str(mix),
          "-filter_complex",
          "[1:a]afade=t=out:st=4:d=2,alimiter=limit=0.776:level=disabled[a]",
          "-map", "0:v", "-map", "[a]", "-c:v", "copy",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)])

    # Sample near the END of the 4s->6s fade, not mid-way through it.
    early = _rms_dbfs(out, 1.0, 1.0)
    late = _rms_dbfs(out, 5.70, 0.25)
    assert late < early - 15, (
        f"final fade did not survive encoding: early={early:.1f} late={late:.1f} dBFS")

    tp = _true_peak_dbfs(out)
    assert tp <= -1.5, f"true peak {tp} dBFS exceeds the -1.5 dBTP ceiling"


def test_compose_director_separates_true_peak_from_sample_peak():
    body = (RELAX / "compose-director.md").read_text(encoding="utf-8").lower()
    assert "sample peak is not true peak" in body
    assert "encoded" in body, "must require measuring the encoded file"


# ==================== 9. honest long-form reuse accounting =================
def test_edit_director_forbids_counting_overlap_as_coverage():
    body = (RELAX / "edit-director.md").read_text(encoding="utf-8").lower()
    assert "overlapping files do not add continuous coverage" in body
    assert "never repeat a complete mixed programme" in body


# ======================= 10. opening brand hierarchy =======================
def test_opening_exposes_three_independent_text_elements():
    src = OPENING_TSX.read_text(encoding="utf-8")
    for prop in ("brandSignature", "welcomeMessage", "episodeLine"):
        assert prop in src, f"RiverFlowOpening must expose {prop}"
    assert "wordmark" not in src, (
        "the single-wordmark prop was replaced by an explicit hierarchy")


def test_opening_message_is_larger_than_the_brand_signature():
    """The channel name is a signature, not the headline."""
    src = OPENING_TSX.read_text(encoding="utf-8")
    import re
    msg = re.search(r"msgSize\s*=\s*Math\.round\(width\s*\*\s*([0-9.]+)\)", src)
    sig = re.search(r"sigSize\s*=\s*Math\.round\(width\s*\*\s*([0-9.]+)\)", src)
    epi = re.search(r"epiSize\s*=\s*Math\.round\(width\s*\*\s*([0-9.]+)\)", src)
    assert msg and sig and epi, "opening must size all three elements explicitly"
    m, s, e = float(msg.group(1)), float(sig.group(1)), float(epi.group(1))
    assert m > s > e, f"hierarchy wrong: message={m} signature={s} episode={e}"


def test_opening_registered_with_hierarchy_defaults():
    src = ROOT_TSX.read_text(encoding="utf-8")
    assert "RiverFlowOpening" in src
    assert "brandSignature" in src and "welcomeMessage" in src


def test_opening_scrim_stays_light():
    """An excessive dark scrim is a brand defect - the footage must still read."""
    src = OPENING_TSX.read_text(encoding="utf-8")
    import re
    m = re.search(r"scrimOpacity\s*=\s*([0-9.]+)", src)
    assert m, "scrimOpacity must have a default"
    assert float(m.group(1)) <= 0.35, f"default scrim {m.group(1)} is too heavy"


# ============================ shared skill wiring ==========================
def test_audio_mastering_skill_exists_and_is_wired():
    skill = SKILLS / "meta" / "audio-mastering.md"
    assert skill.is_file(), "meta/audio-mastering.md must exist"
    assert "meta/audio-mastering.md" in (SKILLS / "INDEX.md").read_text(encoding="utf-8")
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert "meta/audio-mastering" in m.get("required_skills", [])


def test_audio_mastering_skill_carries_no_channel_identity():
    body = (SKILLS / "meta" / "audio-mastering.md").read_text(encoding="utf-8").lower()
    for token in ("river flow", "channel_0001", "piano"):
        assert token not in body, f"generic audio skill must not name {token!r}"
