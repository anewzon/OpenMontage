"""The delivery contract and the technical QC that blocks a non-conforming master.

Masters are synthesised with FFmpeg at a small canvas so the suite stays
fast; the contract is the same one a 4K production uses, scaled. Each defect
the audit found or the compose Director warns about is planted and must
block: the video_0003 opening (yuvj420p, full range, BT.601), black and
frozen spans, loudness off target, a second audio stream, and audio that
does not run the length of the picture.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.delivery_qc import (
    DeliveryContract,
    DeliveryContractError,
    compare_segments,
    conform,
    delivery_filter,
    delivery_qc,
    encode_args,
    probe,
    remotion_render_args,
    render_opening,
)
from lib.stem_balance import measure_stem

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

W, H, FPS = 640, 360, 30
CANVAS = {"width": W, "height": H, "fps": FPS, "pix_fmt": "yuv420p", "codec": "h264",
          "audio": "aac 48 kHz stereo, -16 LUFS, true peak <= -1.5 dBTP"}


def contract(seconds: float = 12.0) -> DeliveryContract:
    return DeliveryContract.from_proposal(
        {"metadata": {"delivery_canvas": CANVAS, "target_duration_seconds": seconds}})


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def picture(path: Path, seconds: float, *, source: str = "testsrc2") -> Path:
    c = contract(seconds)
    sep = ":" if "=" in source else "="
    run("-f", "lavfi", "-i", f"{source}{sep}size={W}x{H}:rate={FPS}:duration={seconds}",
        "-vf", delivery_filter(c), *encode_args(c, audio=False), str(path))
    return path


def audio_at(path: Path, seconds: float, lufs: float = -16.0) -> Path:
    raw = path.with_suffix(".raw.wav")
    run("-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={seconds}:seed=5:amplitude=0.2",
        "-ac", "2", "-ar", "48000", str(raw))
    gain = lufs - measure_stem(raw, short_term=False).integrated_lufs
    run("-i", str(raw), "-af", f"volume={gain}dB", "-ar", "48000", str(path))
    return path


def master(tmp: Path, name: str, video: Path, audio: Path, *extra: str) -> Path:
    out = tmp / name
    run("-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", *extra,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2", str(out))
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("delivery")
    body = picture(tmp / "body.mp4", 12)
    sound = audio_at(tmp / "sound.wav", 12)
    return {"tmp": tmp, "body": body, "sound": sound,
            "good": master(tmp, "good.mp4", body, sound)}


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------


class TestContract:
    def test_it_is_read_from_the_proposal_canvas(self):
        c = contract()
        assert (c.width, c.height, c.fps) == (W, H, "30/1")
        assert (c.integrated_lufs, c.true_peak_max_dbtp) == (-16.0, -1.5)
        assert (c.color_primaries, c.color_space, c.color_range, c.pix_fmt, c.sar) == \
            ("bt709", "bt709", "tv", "yuv420p", "1:1")
        assert (c.sample_rate, c.channels) == (48000, 2)

    def test_the_video_0003_canvas_sentence_parses(self):
        canvas = {"width": 3840, "height": 2160, "fps": 30, "pix_fmt": "yuv420p",
                  "codec": "h264", "audio": "aac 48 kHz stereo, -16 LUFS, true peak <= -1.5 dBTP"}
        c = DeliveryContract.from_proposal({"metadata": {"delivery_canvas": canvas,
                                                         "target_duration_seconds": 120.0}})
        assert (c.width, c.height, c.integrated_lufs, c.true_peak_max_dbtp) == \
            (3840, 2160, -16.0, -1.5)

    @pytest.mark.parametrize("canvas", [
        None,
        {"width": 640, "height": 360},
        {**CANVAS, "audio": "aac 48 kHz stereo"},
        {**CANVAS, "color_space": "bt601"},
        {**CANVAS, "pix_fmt": "yuvj420p"},
    ])
    def test_an_unusable_canvas_is_refused(self, canvas):
        with pytest.raises(DeliveryContractError):
            DeliveryContract.from_proposal({"metadata": {"delivery_canvas": canvas}})

    def test_the_remotion_opening_is_rendered_at_the_standard(self):
        args = remotion_render_args(contract())
        assert "--pixel-format=yuv420p" in args and "--color-space=bt709" in args

    def test_encode_args_tag_the_colour_explicitly(self):
        args = encode_args(contract())
        for flag, value in (("-colorspace", "bt709"), ("-color_primaries", "bt709"),
                            ("-color_trc", "bt709"), ("-color_range", "tv"),
                            ("-pix_fmt", "yuv420p")):
            assert args[args.index(flag) + 1] == value


# --------------------------------------------------------------------------
# QC on real files
# --------------------------------------------------------------------------


class TestDeliveryQc:
    def test_a_conforming_master_passes_every_check(self, media):
        report = delivery_qc(media["good"], contract(12))
        assert report["passed"], report["blockers"]
        names = {c["check"] for c in report["checks"]}
        for required in ("width", "height", "fps", "pix_fmt", "color_primaries",
                         "color_transfer", "color_space", "color_range", "sar",
                         "duration_seconds", "audio_streams", "sample_rate", "channels",
                         "integrated_lufs", "true_peak_dbtp", "black_frames", "frozen_frames",
                         "av_duration_difference_seconds", "clean_decode"):
            assert required in names, required

    def test_the_video_0003_opening_format_blocks_and_conform_fixes_it(self, media):
        tmp = media["tmp"]
        opening = tmp / "opening_yuvj.mp4"
        run("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=3",
            "-vf", "scale=out_range=full:out_color_matrix=bt601,format=yuvj420p",
            "-c:v", "libx264", "-pix_fmt", "yuvj420p", "-color_range", "pc",
            "-colorspace", "smpte170m", "-color_primaries", "bt470bg", "-color_trc", "smpte170m",
            str(opening))
        problems = compare_segments(opening, media["body"], contract())
        assert any("pix_fmt" in p for p in problems)
        assert any("color_range" in p for p in problems)
        assert any("color_space" in p for p in problems)
        fixed = tmp / "opening_conformed.mp4"
        conform(opening, fixed, contract())
        assert compare_segments(fixed, media["body"], contract()) == []

    def test_a_master_joined_from_mismatched_pieces_is_blocked(self, media):
        tmp = media["tmp"]
        opening = tmp / "opening_bad.mp4"
        run("-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuvj420p", "-color_range", "pc", str(opening))
        report = delivery_qc(media["good"], contract(12), opening=opening, body=media["body"])
        assert not report["passed"]
        assert any(b.startswith("opening_body_compatible") for b in report["blockers"])

    def test_wrong_canvas_and_frame_rate_block(self, media):
        tmp = media["tmp"]
        small = tmp / "small.mp4"
        run("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(small))
        report = delivery_qc(master(tmp, "small_master.mp4", small, media["sound"]), contract(12))
        failed = {c["check"] for c in report["checks"] if not c["passed"]}
        assert {"width", "height", "fps"} <= failed

    def test_black_and_frozen_spans_block(self, media):
        tmp = media["tmp"]
        parts = [picture(tmp / "p1.mp4", 4), picture(tmp / "black.mp4", 2, source="color=c=black")]
        frozen = tmp / "frozen.mp4"
        run("-f", "lavfi", "-i", f"color=c=gray:size={W}x{H}:rate={FPS}:duration=6",
            "-vf", delivery_filter(contract()), *encode_args(contract(), audio=False),
            str(frozen))
        parts.append(frozen)
        listing = tmp / "parts.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        joined = tmp / "joined.mp4"
        run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined))
        report = delivery_qc(master(tmp, "bf.mp4", joined, media["sound"]), contract(12))
        failed = {c["check"] for c in report["checks"] if not c["passed"]}
        assert {"black_frames", "frozen_frames"} <= failed

    def test_loudness_off_target_blocks(self, media):
        loud = audio_at(media["tmp"] / "loud.wav", 12, lufs=-10.0)
        report = delivery_qc(master(media["tmp"], "loud.mp4", media["body"], loud), contract(12))
        failed = {c["check"] for c in report["checks"] if not c["passed"]}
        assert "integrated_lufs" in failed

    def test_a_second_audio_stream_blocks(self, media):
        tmp = media["tmp"]
        out = tmp / "two_audio.mp4"
        run("-i", str(media["good"]), "-i", str(media["sound"]), "-map", "0:v", "-map", "0:a",
            "-map", "1:a", "-c:v", "copy", "-c:a", "aac", str(out))
        report = delivery_qc(out, contract(12))
        assert any(b.startswith("audio_streams") for b in report["blockers"])

    def test_audio_shorter_than_the_picture_blocks(self, media):
        short = audio_at(media["tmp"] / "short.wav", 10.5)
        report = delivery_qc(master(media["tmp"], "short.mp4", media["body"], short), contract(12))
        assert any(b.startswith("av_duration_difference") for b in report["blockers"])

    def test_duration_far_from_the_plan_blocks(self, media):
        report = delivery_qc(media["good"], contract(60))
        assert any(b.startswith("duration_seconds") for b in report["blockers"])

    def test_the_report_fits_render_report_metadata(self, media):
        import json

        report = delivery_qc(media["good"], contract(12))
        json.dumps(report)
        assert probe(media["good"])["format"]["format_name"].startswith("mov")


COMPOSER = Path(__file__).resolve().parents[2] / "remotion-composer"


@pytest.mark.skipif(not (COMPOSER / "node_modules" / ".bin").is_dir(),
                    reason="remotion-composer dependencies not installed")
def test_a_real_remotion_opening_matches_a_body_encoded_to_the_contract(media, tmp_path):
    """The Remotion opening, rendered with the contract, joins the body without mismatch."""
    c = DeliveryContract.from_proposal({"metadata": {"delivery_canvas": {
        **CANVAS, "width": 1280, "height": 720}, "target_duration_seconds": 60}})
    body = tmp_path / "body720.mp4"
    run("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
        "-vf", delivery_filter(c), *encode_args(c, audio=False), str(body))
    result = render_opening(
        composer_dir=COMPOSER, composition="RiverFlowOpening",
        props={"videoSrc": "", "brandSignature": "Test Channel", "welcomeMessage": "HELLO",
               "durationSeconds": 2},
        output=tmp_path / "opening.mp4", contract=c)
    assert compare_segments(result["output"], body, c) == [], result
    assert (result["format"]["width"], result["format"]["height"]) == (1280, 720)
