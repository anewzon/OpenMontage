"""Channel-owned persistent overlays: declared by a channel, composited by generic code.

Covers the required cases: the live channel_0002 subscribe overlay resolves
and schedules; River Flow declares none and gets none; a synthetic channel
with a different overlay works through the same code; a missing declared
asset stops production; unusable media is blocked before render; an asset's
audio never enters the programme; alpha and non-alpha sources are handled
from measured properties; the delivered piece still passes the delivery
contract; and no channel name, filename or timing is hard-coded in generic
code. Overlay media for the synthetic cases is rendered here with FFmpeg.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.channel_overlay import (
    OverlayError,
    channel_asset_provenance,
    composite_overlay,
    inspect_media,
    overlay_policies_from_data,
    overlay_qc,
    provenance_for,
    resolve_overlays,
    schedule_overlay,
)
from lib.channel_policy import ChannelPolicyError, channel_policy_from_brand, load_channel
from lib.delivery_qc import DeliveryContract, delivery_filter, delivery_qc, encode_args
from lib.relaxation_publish_gate import licence_report
from tests._paths import channel_dir

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "relaxation" / "channels"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
W, H = 640, 360
CONTRACT = DeliveryContract(width=W, height=H, fps="30/1", integrated_lufs=-16.0,
                            true_peak_max_dbtp=-1.5, loudness_tolerance_lu=60.0)


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def make_overlay(path: Path, *, alpha: bool, audio: bool = False, seconds: float = 2.0,
                 size: str = "320x180") -> Path:
    """A small animated badge: transparent canvas (or opaque black) with a red box."""
    w, h = (int(v) for v in size.split("x"))
    # An opaque red box padded onto a transparent (or black) canvas. drawbox
    # would leave the alpha plane at zero, i.e. an invisible overlay.
    canvas = "black@0.0" if alpha else "black"
    fmt = "yuva444p10le" if alpha else "yuv422p10le"
    inputs = ["-f", "lavfi", "-i",
              f"color=c=red:s={w * 3 // 4}x{h // 2}:r=30:d={seconds},format=rgba,"
              f"pad={w}:{h}:{w // 8}:{h // 4}:color={canvas}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=1000:duration={seconds}"]
    ff(*inputs, "-c:v", "prores_ks", "-profile:v", "4444" if alpha else "3", "-pix_fmt", fmt,
       *(["-c:a", "pcm_s16le"] if audio else []), str(path))
    return path


def make_body(path: Path, seconds: float = 12.0, audio: bool = True) -> Path:
    inputs = ["-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30:duration={seconds}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}"]
    ff(*inputs, "-vf", delivery_filter(CONTRACT), *encode_args(CONTRACT, audio=audio),
       "-shortest", str(path))
    return path


def synthetic_channel(root: Path, channel_id: str, overlay_yaml: str, *, base: str = "forest",
                      provenance: bool = True) -> Path:
    """A channel folder built from a fixture, with an overlays: list appended."""
    folder = root / channel_id
    shutil.copytree(FIXTURES / base, folder)
    brand = folder / "BRAND.md"
    text = brand.read_text(encoding="utf-8")
    text = text.replace(f"channel_id: {load_channel(FIXTURES / base).profile.channel_id}",
                        f"channel_id: {channel_id}")
    marker = "```\n\n## Channel mix settings"
    assert marker in text
    text = text.replace(marker, overlay_yaml.rstrip("\n") + "\n```\n\n## Channel mix settings")
    brand.write_text(text, encoding="utf-8")
    (folder / "brand_assets" / "overlays").mkdir(parents=True, exist_ok=True)
    if provenance:
        (folder / "brand_assets" / "PROVENANCE.md").write_text(
            "# Provenance\n\n```channel-provenance\n"
            "overlays/badge.mov:\n  licence: made in-house\n  source: fixture\n"
            "  verified_by: test\n  date: 2026-09-23\n```\n", encoding="utf-8")
    return folder


BADGE = """overlays:
  - id: badge
    enabled: true
    asset: brand_assets/overlays/badge.mov
    purpose: corner bug
    usage: once_per_video
    placement: bottom_right
    scale: 0.2
"""
SLOTS = [{"id": "S1", "start_seconds": 0, "end_seconds": 4, "shot_scale": "wide",
          "subject_motion": "gentle"},
         {"id": "S2", "start_seconds": 4, "end_seconds": 12, "shot_scale": "medium",
          "subject_motion": "still"}]


# --------------------------------------------------------------------------
# Declaration
# --------------------------------------------------------------------------


class TestDeclaration:
    def test_a_channel_with_no_overlays_key_declares_none(self):
        policy = channel_policy_from_brand((FIXTURES / "river_flow" / "BRAND.md").read_text(
            encoding="utf-8"))
        assert policy.overlays == () and policy.to_metadata()["overlays"] == []

    @pytest.mark.parametrize("edit,why", [
        (lambda s: s.replace("placement: bottom_right", "placement: everywhere"), "placement"),
        (lambda s: s.replace("    enabled: true\n", ""), "missing required"),
        (lambda s: s.replace("brand_assets/overlays/badge.mov", "../../elsewhere.mov"),
         "relative to the channel"),
        (lambda s: s.replace("usage: once_per_video", "usage: whenever"), "usage"),
        (lambda s: s + "    colour: red\n", "unknown key"),
        (lambda s: s.replace("scale: 0.2", "scale: 4"), "fraction"),
    ])
    def test_invalid_declarations_are_rejected(self, edit, why):
        import yaml

        with pytest.raises(OverlayError, match=re.escape(why)):
            overlay_policies_from_data(yaml.safe_load(edit(BADGE))["overlays"])

    def test_the_policy_parser_surfaces_overlay_errors(self):
        text = (FIXTURES / "forest" / "BRAND.md").read_text(encoding="utf-8").replace(
            "```\n\n## Channel mix settings",
            "overlays:\n  - id: x\n    enabled: true\n    asset: a.mov\n    purpose: p\n"
            "    usage: sometimes\n```\n\n## Channel mix settings")
        with pytest.raises(ChannelPolicyError, match="usage"):
            channel_policy_from_brand(text)


# --------------------------------------------------------------------------
# The live channels
# --------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_channel_0002_subscribe_overlay_is_resolved_and_scheduled():
    folder = channel_dir("channel_0002")
    if not (folder / "BRAND.md").is_file():
        pytest.skip("channel_0002 not present")
    channel = load_channel(folder, expected_id="channel_0002")
    assert len(channel.policy.overlays) == 1
    declared = channel.policy.overlays[0]
    assert declared.purpose.lower().startswith("subscribe") and declared.usage == "once_per_video"
    assert declared.audio == "exclude" and declared.alter == "none" and declared.preserve_proportions
    asset = folder / declared.asset
    if not asset.is_file():
        pytest.skip("the channel's overlay asset is not on this machine")
    resolved = resolve_overlays(channel.policy.overlays, folder, frame=(3840, 2160))
    media = resolved[0].media
    # Measured, not assumed: the file is what ffprobe says it is.
    assert (media.width, media.height) == (3840, 2160) and media.fps == 30.0
    assert media.codec == "prores" and media.pix_fmt.startswith("yuva")
    assert media.has_alpha_plane and media.alpha_varies, "the alpha plane must really vary"
    assert media.audio_streams == 0
    record = schedule_overlay(resolved[0], runtime_seconds=180, opening_seconds=8, slots=[
        {"id": "S1", "start_seconds": 8, "end_seconds": 60, "shot_scale": "wide",
         "subject_motion": "gentle"},
        {"id": "S2", "start_seconds": 60, "end_seconds": 180, "shot_scale": "detail",
         "subject_motion": "strong"}], frame=(3840, 2160))
    assert record["slot_id"] == "S1" and record["start_seconds"] >= 9.0
    assert record["end_seconds"] <= 180 - 5 and record["region"] == {"x": 0, "y": 0, "w": 3840,
                                                                       "h": 2160}
    assert record["audio"] == "excluded" and record["asset_sha256"] == media.sha256


def test_river_flow_declares_no_overlay_and_gets_none():
    folder = channel_dir("channel_0001")
    if not (folder / "BRAND.md").is_file():
        pytest.skip("channel_0001 not present")
    channel = load_channel(folder, expected_id="channel_0001")
    assert channel.policy.overlays == ()
    assert resolve_overlays(channel.policy.overlays, folder, frame=(3840, 2160)) == []
    assert "overlays" not in channel.brand_text.split("```channel-policy")[1].split("```")[0]
    # A River Flow edit carries no overlay record, and QC on it expects none.
    assert licence_report(Path("."), {"assets": []}, {"cuts": [], "metadata": {}},
                          channel_root=folder)["blockers"] == []


# --------------------------------------------------------------------------
# The generic mechanism on a synthetic channel
# --------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
class TestGenericMechanism:
    def test_another_channel_with_a_different_overlay_composites_and_passes_qc(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0077", BADGE)
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True)
        channel = load_channel(folder, expected_id="channel_0077")
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        assert resolved[0].media.has_alpha_plane and resolved[0].media.alpha_varies
        record = schedule_overlay(resolved[0], runtime_seconds=12, opening_seconds=0,
                                  slots=SLOTS, frame=(W, H))
        assert record["slot_id"] == "S2" and record["region"]["w"] == round(W * 0.2)
        assert abs(record["region"]["w"] / record["region"]["h"] - 320 / 180) < 0.02
        body = make_body(tmp_path / "body.mp4")
        out = tmp_path / "with_badge.mp4"
        extended = composite_overlay(body, out, record, CONTRACT)
        ev = extended["evidence"]
        assert ev["frame_diff_inside_window"] > 3 * ev["frame_diff_outside_window"]
        assert ev["output_audio_streams"] == 1 and ev["overlay_audio_mapped"] is False
        qc = overlay_qc(out, [extended], CONTRACT, expected_ids=["badge"])
        assert qc["passed"], qc["blockers"]
        technical = delivery_qc(out, CONTRACT)
        assert technical["passed"], technical["blockers"]
        edit = {"cuts": [], "metadata": {"overlays": [extended]}}
        report = licence_report(tmp_path, {"assets": []}, edit, channel_root=folder)
        assert report["blockers"] == [] and "overlay:badge" in report["verified"]

    def test_a_missing_declared_overlay_stops_production(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0078", BADGE)
        channel = load_channel(folder, expected_id="channel_0078")
        with pytest.raises(OverlayError, match="never silently omitted"):
            resolve_overlays(channel.policy.overlays, folder, frame=(W, H))

    def test_unusable_overlay_media_is_blocked_before_render(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0079", BADGE)
        bad = folder / "brand_assets" / "overlays" / "badge.mov"
        bad.write_bytes(b"not a movie at all")
        channel = load_channel(folder, expected_id="channel_0079")
        with pytest.raises(OverlayError, match="cannot be probed|no video stream|no usable"):
            resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        # An audio-only file is not an overlay either.
        ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(bad))
        with pytest.raises(OverlayError, match="no video stream"):
            inspect_media(bad)

    def test_overlay_audio_is_excluded_from_the_programme(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0080", BADGE)
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True, audio=True)
        channel = load_channel(folder, expected_id="channel_0080")
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        assert resolved[0].media.audio_streams == 1
        record = schedule_overlay(resolved[0], runtime_seconds=12, opening_seconds=0,
                                  slots=SLOTS, frame=(W, H))
        assert record["audio"] == "excluded" and record["source_audio_streams"] == 1
        body = make_body(tmp_path / "body.mp4")
        extended = composite_overlay(body, tmp_path / "out.mp4", record, CONTRACT)
        assert extended["evidence"]["output_audio_streams"] == 1
        # The programme audio is the body's 220 Hz tone, not the overlay's 1 kHz tone.
        stats = subprocess.run(
            ["ffmpeg", "-v", "info", "-ss", str(record["start_seconds"] + 0.5), "-t", "0.5",
             "-i", str(tmp_path / "out.mp4"), "-af", "highpass=f=700,volumedetect", "-f", "null",
             "-"], capture_output=True, text=True).stderr
        mean = re.search(r"mean_volume: (-?[\d.]+) dB", stats)
        assert mean and float(mean.group(1)) < -40, "1 kHz overlay audio leaked into the mix"
        assert overlay_qc(tmp_path / "out.mp4", [extended], CONTRACT)["passed"]
        # A body with no audio stays without audio: nothing is invented either.
        silent = make_body(tmp_path / "silent.mp4", audio=False)
        ext2 = composite_overlay(silent, tmp_path / "out2.mp4", record, CONTRACT)
        assert ext2["evidence"]["output_audio_streams"] == 0

    def test_alpha_and_non_alpha_sources_are_handled_from_measured_properties(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0081", BADGE)
        badge = folder / "brand_assets" / "overlays" / "badge.mov"
        make_overlay(badge, alpha=False)
        opaque = inspect_media(badge)
        assert not opaque.has_alpha_plane and not opaque.alpha_varies
        make_overlay(badge, alpha=True)
        transparent = inspect_media(badge)
        assert transparent.has_alpha_plane and transparent.alpha_varies
        # A .mov whose alpha plane is fully opaque is not transparency.
        ff("-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=1,format=rgba", "-c:v",
           "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(badge))
        promised = inspect_media(badge)
        assert promised.has_alpha_plane and not promised.alpha_varies
        channel = load_channel(folder, expected_id="channel_0081")
        make_overlay(badge, alpha=False)
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        record = schedule_overlay(resolved[0], runtime_seconds=12, opening_seconds=0,
                                  slots=SLOTS, frame=(W, H))
        body = make_body(tmp_path / "body.mp4")
        extended = composite_overlay(body, tmp_path / "out.mp4", record, CONTRACT)
        assert "format=yuv420p" in extended["evidence"]["filter_graph"]
        assert overlay_qc(tmp_path / "out.mp4", [extended], CONTRACT)["passed"]

    def test_a_full_frame_asset_of_another_shape_is_refused(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0082",
                                   BADGE.replace("placement: bottom_right", "placement: as_authored")
                                   .replace("    scale: 0.2\n", ""))
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True,
                     size="320x320")
        channel = load_channel(folder, expected_id="channel_0082")
        with pytest.raises(OverlayError, match="proportions preserved"):
            resolve_overlays(channel.policy.overlays, folder, frame=(W, H))

    def test_a_piece_too_short_for_the_overlay_is_an_error_not_an_omission(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0083", BADGE)
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True, seconds=8)
        channel = load_channel(folder, expected_id="channel_0083")
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        with pytest.raises(OverlayError, match="cannot fit"):
            schedule_overlay(resolved[0], runtime_seconds=10, opening_seconds=3, slots=SLOTS,
                             frame=(W, H))
        # Short but long enough: the fallback window is used and recorded as such.
        record = schedule_overlay(resolved[0], runtime_seconds=16, opening_seconds=2,
                                  slots=[{"id": "S1", "start_seconds": 0, "end_seconds": 5},
                                         {"id": "S2", "start_seconds": 5, "end_seconds": 16}],
                                  frame=(W, H))
        assert record["slot_id"] is None and "short piece" in record["rationale"]
        assert record["start_seconds"] == 3.0 and record["end_seconds"] == 11.0

    def test_qc_catches_an_omitted_or_unevidenced_overlay(self, tmp_path):
        body = make_body(tmp_path / "body.mp4")
        qc = overlay_qc(body, [], CONTRACT, expected_ids=["subscribe"])
        assert not qc["passed"] and "omitted" in qc["blockers"][0]
        folder = synthetic_channel(tmp_path, "channel_0084", BADGE)
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True)
        channel = load_channel(folder, expected_id="channel_0084")
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        record = schedule_overlay(resolved[0], runtime_seconds=12, opening_seconds=0,
                                  slots=SLOTS, frame=(W, H))
        qc = overlay_qc(body, [record], CONTRACT)
        assert not qc["passed"] and any("never composited" in b for b in qc["blockers"])
        # A record whose asset changed since the edit cannot be composited.
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=False)
        with pytest.raises(OverlayError, match="changed since"):
            composite_overlay(body, tmp_path / "x.mp4", record, CONTRACT)

    def test_the_composite_is_deterministic_from_the_record(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0085", BADGE)
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True)
        channel = load_channel(folder, expected_id="channel_0085")
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        record = schedule_overlay(resolved[0], runtime_seconds=12, opening_seconds=0,
                                  slots=SLOTS, frame=(W, H))
        record = json.loads(json.dumps(record))      # survives the checkpoint round-trip
        body = make_body(tmp_path / "body.mp4")
        a = composite_overlay(body, tmp_path / "a.mp4", record, CONTRACT)
        b = composite_overlay(body, tmp_path / "b.mp4", record, CONTRACT)
        assert a["evidence"]["filter_graph"] == b["evidence"]["filter_graph"]
        assert a["start_seconds"] == b["start_seconds"] == record["start_seconds"]

    def test_a_chunk_offset_places_the_overlay_on_the_programme_timeline(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0086", BADGE)
        make_overlay(folder / "brand_assets" / "overlays" / "badge.mov", alpha=True)
        channel = load_channel(folder, expected_id="channel_0086")
        resolved = resolve_overlays(channel.policy.overlays, folder, frame=(W, H))
        record = schedule_overlay(resolved[0], runtime_seconds=612, opening_seconds=0, slots=[
            {"id": "S9", "start_seconds": 600, "end_seconds": 612, "shot_scale": "wide",
             "subject_motion": "still"}], frame=(W, H))
        assert 600 <= record["start_seconds"] < 612
        chunk = make_body(tmp_path / "chunk.mp4")        # the chunk that starts at 600 s
        extended = composite_overlay(chunk, tmp_path / "chunk_out.mp4", record, CONTRACT,
                                     piece_offset_seconds=600)
        assert extended["evidence"]["frame_diff_inside_window"] > 1.0


# --------------------------------------------------------------------------
# Provenance and the gate
# --------------------------------------------------------------------------


class TestProvenance:
    def test_missing_provenance_is_reported_not_invented(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0087", BADGE, provenance=False)
        (folder / "brand_assets" / "overlays" / "badge.mov").write_bytes(b"x")
        ok, why = provenance_for(folder, "brand_assets/overlays/badge.mov")
        assert ok is None and "no provenance evidence" in why
        edit = {"cuts": [], "metadata": {"overlays": [
            {"id": "badge", "asset": "brand_assets/overlays/badge.mov",
             "channel_root": str(folder)}]}}
        report = licence_report(tmp_path, {"assets": []}, edit, channel_root=folder)
        assert not report["passed"] and report["blockers"][0].startswith("overlay:badge")

    def test_provenance_is_read_once_from_the_channel_and_checks_the_file(self, tmp_path):
        folder = synthetic_channel(tmp_path, "channel_0088", BADGE)
        asset = folder / "brand_assets" / "overlays" / "badge.mov"
        asset.write_bytes(b"movie")
        entries = channel_asset_provenance(folder)
        assert "overlays/badge.mov" in entries
        ok, why = provenance_for(folder, "brand_assets/overlays/badge.mov")
        assert why is None and "made in-house" in ok
        prov = folder / "brand_assets" / "PROVENANCE.md"
        prov.write_text(prov.read_text(encoding="utf-8").replace(
            "  date: 2026-09-23\n", "  date: 2026-09-23\n  sha256: 0000\n"), encoding="utf-8")
        ok, why = provenance_for(folder, "brand_assets/overlays/badge.mov")
        assert ok is None and "changed since" in why
        prov.write_text(prov.read_text(encoding="utf-8").replace("  sha256: 0000\n",
                                                                 "  receipt: r.pdf\n"),
                        encoding="utf-8")
        ok, why = provenance_for(folder, "brand_assets/overlays/badge.mov")
        assert ok is None and "receipt" in why

    def test_the_live_subscribe_overlay_provenance_status_is_reported(self):
        """Documents the state of the real asset: verified, or exactly what is missing."""
        folder = channel_dir("channel_0002")
        if not (folder / "BRAND.md").is_file():
            pytest.skip("channel_0002 not present")
        channel = load_channel(folder, expected_id="channel_0002")
        ok, why = provenance_for(folder, channel.policy.overlays[0].asset)
        assert (ok is None) != (why is None)
        if why:
            assert "provenance" in why or "lacks" in why


# --------------------------------------------------------------------------
# Nothing channel-specific in generic code
# --------------------------------------------------------------------------


def test_generic_overlay_code_names_no_channel_asset_or_timing():
    generic = [ROOT / "lib" / "channel_overlay.py", ROOT / "lib" / "channel_policy.py",
               ROOT / "pipeline_defs" / "relaxation.yaml"]
    generic += list((ROOT / "skills" / "pipelines" / "relaxation").glob("*.md"))
    # The gate and delivery QC docstrings cite the production incident that
    # motivated them (a channel_0001 project); that history is not policy.
    history = [ROOT / "lib" / "relaxation_publish_gate.py", ROOT / "lib" / "delivery_qc.py"]
    banned = ("subscribe_button", "channel_0002", "unseen america", "at 60 seconds",
              "always at")
    identity = ("channel_0001", "river flow naturescapes")
    for path in generic + history:
        text = path.read_text(encoding="utf-8").lower()
        for token in banned + (identity if path in generic else ()):
            assert token not in text, f"{path.name} hard-codes {token!r}"
    src = (ROOT / "lib" / "channel_overlay.py").read_text(encoding="utf-8")
    assert not re.search(r"start_seconds\s*=\s*\d", src), "no fixed overlay timestamp"
