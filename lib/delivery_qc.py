"""The delivery contract, and the technical QC that blocks a non-conforming master.

A relaxation master is rendered in pieces - a Remotion opening and an FFmpeg
body - and concatenated. On channel_0001__video_0003 the opening came out
``yuvj420p``, full range, BT.601 while the body was ``yuv420p``, limited
range, BT.709: nothing checked it. This module makes the target explicit and
the check executable.

* `DeliveryContract.from_proposal` reads the canvas and audio targets the
  proposal recorded (``metadata.delivery_canvas``) and adds the SDR delivery
  standard every piece must share: BT.709 primaries, transfer and matrix,
  limited ("tv") range, ``yuv420p``, square pixels, one AAC stereo track.
* `encode_args`, `conform_filter` and `remotion_render_args` produce pieces
  that meet it; `conform` re-encodes any piece that does not.
* `compare_segments` refuses to join two pieces that disagree.
* `delivery_qc` probes and fully decodes the finished master and returns
  every check with its expected and actual value; any failure is a blocker.

Nothing here knows a channel: the canvas and loudness come from the project.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Optional

from lib.ffmpeg_runtime import ffmpeg_path, ffprobe_path

__all__ = [
    "DeliveryContract",
    "DeliveryContractError",
    "compare_segments",
    "conform",
    "conform_filter",
    "delivery_filter",
    "delivery_qc",
    "encode_args",
    "probe",
    "remotion_render_args",
    "render_opening",
]

#: The colour standard every relaxation piece is delivered in (SDR HD/UHD).
SDR_COLOUR = {"color_primaries": "bt709", "color_transfer": "bt709",
              "color_space": "bt709", "color_range": "tv"}


class DeliveryContractError(ValueError):
    """The project does not state a usable delivery target."""


@dataclass(frozen=True)
class DeliveryContract:
    width: int
    height: int
    fps: str                      # exact rational, e.g. "30/1" or "30000/1001"
    integrated_lufs: float
    true_peak_max_dbtp: float
    duration_seconds: Optional[float] = None
    pix_fmt: str = "yuv420p"
    video_codec: str = "h264"
    color_primaries: str = "bt709"
    color_transfer: str = "bt709"
    color_space: str = "bt709"
    color_range: str = "tv"
    sar: str = "1:1"
    audio_codec: str = "aac"
    sample_rate: int = 48000
    channels: int = 2
    loudness_tolerance_lu: float = 1.0
    duration_tolerance_seconds: float = 2.0
    av_duration_tolerance_seconds: float = 0.1
    #: Black is allowed only inside this many seconds at either end (fades).
    black_allowance_seconds: float = 2.0
    black_min_seconds: float = 0.5
    freeze_min_seconds: float = 5.0

    @property
    def fps_fraction(self) -> Fraction:
        return Fraction(self.fps)

    @classmethod
    def from_proposal(cls, proposal_packet: Mapping[str, Any]) -> "DeliveryContract":
        """Build from ``proposal_packet.metadata.delivery_canvas`` - no defaults for taste.

        Canvas (width, height, fps) and loudness (integrated LUFS, true-peak
        ceiling) must be stated; loudness may be structured keys or the
        canvas's ``audio`` sentence ("aac 48 kHz stereo, -16 LUFS, true peak
        <= -1.5 dBTP"). The colour standard is fixed; a canvas that asks for
        anything else is refused rather than silently mixed.
        """
        meta = proposal_packet.get("metadata") or {}
        canvas = meta.get("delivery_canvas")
        if not isinstance(canvas, Mapping):
            raise DeliveryContractError("proposal_packet.metadata.delivery_canvas is missing")
        try:
            width, height = int(canvas["width"]), int(canvas["height"])
            fps = str(Fraction(str(canvas["fps"])).limit_denominator(1001))
        except (KeyError, TypeError, ValueError) as exc:
            raise DeliveryContractError(f"delivery_canvas needs width, height and fps: {exc}")
        if "/" not in fps:
            fps = f"{fps}/1"
        audio_text = str(canvas.get("audio") or "")
        lufs = canvas.get("integrated_lufs")
        if lufs is None:
            match = re.search(r"(-\d+(?:\.\d+)?)\s*LUFS", audio_text)
            lufs = float(match.group(1)) if match else None
        peak = canvas.get("true_peak_max_dbtp")
        if peak is None:
            match = re.search(r"true[\s-]*peak\s*(?:<=|≤|<|max)?\s*(-\d+(?:\.\d+)?)\s*dBTP",
                              audio_text, re.I)
            peak = float(match.group(1)) if match else None
        if lufs is None or peak is None:
            raise DeliveryContractError(
                "delivery_canvas states no integrated loudness and true-peak ceiling")
        rate = canvas.get("sample_rate")
        if rate is None:
            match = re.search(r"(\d+(?:\.\d+)?)\s*kHz", audio_text)
            rate = int(float(match.group(1)) * 1000) if match else 48000
        channels = canvas.get("channels") or (1 if "mono" in audio_text.lower() else 2)
        pix_fmt = canvas.get("pix_fmt", "yuv420p")
        for key, value in SDR_COLOUR.items():
            if canvas.get(key, value) != value:
                raise DeliveryContractError(f"delivery_canvas.{key}={canvas[key]!r}; the "
                                            f"relaxation delivery standard is {value!r}")
        if pix_fmt != "yuv420p":
            raise DeliveryContractError(f"delivery_canvas.pix_fmt={pix_fmt!r}; "
                                        "the delivery standard is yuv420p")
        duration = meta.get("target_duration_seconds")
        return cls(width=width, height=height, fps=fps, integrated_lufs=float(lufs),
                   true_peak_max_dbtp=float(peak),
                   duration_seconds=float(duration) if duration is not None else None,
                   sample_rate=int(rate), channels=int(channels))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Producing conforming pieces
# --------------------------------------------------------------------------


#: H.264 VUI codes for BT.709 (ITU-T H.273): primaries, transfer, matrix = 1.
_H264_BT709_VUI = ("h264_metadata=video_full_range_flag=0:colour_primaries=1:"
                   "transfer_characteristics=1:matrix_coefficients=1")


def encode_args(contract: DeliveryContract, *, audio: bool = True) -> list[str]:
    """FFmpeg output arguments for a piece at the delivery standard.

    Pair with `delivery_filter` (generated or RGB input) or `conform_filter`
    (an existing clip): the FILTER converts the pixels to BT.709 limited range
    and stamps the frames; these arguments encode and write the same colour
    into the H.264 bitstream. Codec-level ``-color_*`` flags alone are not
    enough - FFmpeg 9 takes colour from the frames, and untagged frames came
    out "unknown". And an RGB source converted without an explicit matrix
    becomes BT.601, which is how an opening ends up unlike its body.
    """
    args = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", contract.pix_fmt, "-r", contract.fps,
            "-colorspace", contract.color_space, "-color_primaries", contract.color_primaries,
            "-color_trc", contract.color_transfer, "-color_range", contract.color_range,
            "-bsf:v", _H264_BT709_VUI]
    if audio:
        args += ["-c:a", "aac", "-b:a", "320k", "-ar", str(contract.sample_rate),
                 "-ac", str(contract.channels)]
    else:
        args += ["-an"]
    return args


_MATRIX = {"bt709": "bt709", "smpte170m": "bt601", "bt470bg": "bt601", "unknown": None,
           None: None, "bt2020nc": "bt2020"}


def _stamp(contract: DeliveryContract) -> str:
    return (f"format={contract.pix_fmt},setsar=1,fps={contract.fps},"
            f"setparams=color_primaries={contract.color_primaries}:"
            f"color_trc={contract.color_transfer}:colorspace={contract.color_space}:"
            f"range={contract.color_range}")


def delivery_filter(contract: DeliveryContract) -> str:
    """Video filter for GENERATED or RGB input (graphics, test sources, frames)."""
    return (f"scale={contract.width}:{contract.height}:flags=lanczos:"
            "out_color_matrix=bt709:out_range=limited," + _stamp(contract))


def conform_filter(source_probe: Mapping[str, Any], contract: DeliveryContract) -> str:
    """A video filter converting a probed source to the contract's canvas and colour.

    The source's own matrix and range are read from its probe; an untagged
    source is assumed BT.709 limited unless its pixel format says full range
    (``yuvj*``).
    """
    video = _video_stream(source_probe) or {}
    pix = str(video.get("pix_fmt") or "")
    in_range = video.get("color_range") or ("pc" if pix.startswith("yuvj") else "tv")
    in_matrix = _MATRIX.get(video.get("color_space"), None) or "bt709"
    return (f"scale={contract.width}:{contract.height}:flags=lanczos:"
            f"in_color_matrix={in_matrix}:out_color_matrix=bt709:"
            f"in_range={'full' if in_range == 'pc' else 'limited'}:out_range=limited,"
            + _stamp(contract))


def conform(source: str | Path, output: str | Path, contract: DeliveryContract, *,
            audio: bool = False) -> dict[str, Any]:
    """Re-encode one piece to the delivery standard; returns the output's probe."""
    source, output = Path(source), Path(output)
    graph = conform_filter(probe(source), contract)
    result = subprocess.run(
        [ffmpeg_path(), "-y", "-v", "error", "-i", str(source), "-vf", graph,
         *encode_args(contract, audio=audio), str(output)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise DeliveryContractError(f"conform failed: {result.stderr.strip()[:400]}")
    return probe(output)


def remotion_render_args(contract: DeliveryContract) -> list[str]:
    """Remotion CLI flags for an opening at the delivery standard.

    The canvas itself is passed as the composition's ``width``/``height``/``fps``
    props (see ScenicOpening); these flags fix the encode. The result is
    still conformed and compared before it is joined - a flag is not proof.
    """
    return ["--codec=h264", "--pixel-format=yuv420p", "--color-space=bt709", "--crf=16"]


def render_opening(*, composer_dir: str | Path, composition: str, props: Mapping[str, Any],
                   output: str | Path, contract: DeliveryContract) -> dict[str, Any]:
    """Render a Remotion opening at the contract's canvas, then conform and verify it.

    The contract's width, height and fps are passed as the composition's props
    (they override anything the bed would suggest) and the encode flags come
    from `remotion_render_args`. The Remotion output is then conformed with
    `conform`, because a flag is not proof, and the conformed file is what is
    returned for `compare_segments`. Assets the props reference must already
    be staged in the composer's ``public/`` folder (Remotion cannot read
    ``file:///`` paths).
    """
    composer_dir, output = Path(composer_dir), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    full = {**dict(props), "width": contract.width, "height": contract.height,
            "fps": float(Fraction(contract.fps))}
    props_file = output.with_name(output.stem + ".props.json")
    props_file.write_bytes(json.dumps(full).encode("utf-8"))   # BOM-free, or JSON.parse fails
    raw = output.with_name(output.stem + ".remotion.mp4")
    result = subprocess.run(
        ["npx", "remotion", "render", composition, str(raw), f"--props={props_file}",
         *remotion_render_args(contract)],
        cwd=composer_dir, capture_output=True, text=True, shell=os.name == "nt")
    if result.returncode != 0 or not raw.is_file():
        raise DeliveryContractError(
            f"Remotion render of {composition} failed: {(result.stderr or result.stdout)[-600:]}")
    raw_video = _video_stream(probe(raw)) or {}
    conform(raw, output, contract, audio=False)
    return {"raw": str(raw), "raw_format": _video_facts(raw_video), "output": str(output),
            "format": _video_facts(_video_stream(probe(output)) or {})}


# --------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------


def probe(path: str | Path) -> dict[str, Any]:
    out = subprocess.run(
        [ffprobe_path(), "-v", "error", "-print_format", "json", "-show_streams",
         "-show_format", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        raise DeliveryContractError(f"ffprobe cannot read {path}: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout)


def _video_stream(info: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)


def _streams(info: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    return [s for s in info.get("streams", []) if s.get("codec_type") == kind]


def _video_facts(video: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "width": video.get("width"), "height": video.get("height"),
        "fps": video.get("r_frame_rate"), "pix_fmt": video.get("pix_fmt"),
        "codec": video.get("codec_name"),
        "color_primaries": video.get("color_primaries", "unknown"),
        "color_transfer": video.get("color_transfer", "unknown"),
        "color_space": video.get("color_space", "unknown"),
        "color_range": video.get("color_range", "unknown"),
        "sar": video.get("sample_aspect_ratio", "unknown"),
    }


def _expected_video(contract: DeliveryContract) -> dict[str, Any]:
    return {"width": contract.width, "height": contract.height, "fps": contract.fps,
            "pix_fmt": contract.pix_fmt, "codec": contract.video_codec,
            "color_primaries": contract.color_primaries,
            "color_transfer": contract.color_transfer, "color_space": contract.color_space,
            "color_range": contract.color_range, "sar": contract.sar}


def _same_fps(a: Any, b: Any) -> bool:
    try:
        return Fraction(str(a)) == Fraction(str(b))
    except (ValueError, ZeroDivisionError):
        return False


def compare_segments(opening: str | Path, body: str | Path,
                     contract: DeliveryContract) -> list[str]:
    """Every way two pieces about to be joined disagree with each other or the contract."""
    problems: list[str] = []
    facts = {}
    for label, path in (("opening", opening), ("body", body)):
        video = _video_stream(probe(path))
        if video is None:
            problems.append(f"{label}: no video stream")
            continue
        facts[label] = _video_facts(video)
    if len(facts) < 2:
        return problems
    expected = _expected_video(contract)
    for key, want in expected.items():
        a, b = facts["opening"][key], facts["body"][key]
        same = _same_fps(a, b) if key == "fps" else a == b
        if not same:
            problems.append(f"{key}: opening {a!r} vs body {b!r}")
        for label in ("opening", "body"):
            got = facts[label][key]
            ok = _same_fps(got, want) if key == "fps" else got == want
            if not ok:
                problems.append(f"{label} {key}: {got!r}, contract {want!r}")
    return sorted(set(problems))


def _decode_scan(path: Path) -> dict[str, Any]:
    """One full decode: decode errors, black and frozen spans, loudness and true peak."""
    result = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
         "-map", "0:v:0", "-vf", "blackdetect=d=0.2:pix_th=0.10,freezedetect=n=-60dB:d=2",
         "-map", "0:a:0?", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True)
    log = result.stderr
    errors = [line for line in log.splitlines()
              if re.search(r"(error|corrupt|invalid|non-existing PPS|concealing)", line, re.I)
              and "Summary" not in line]
    black = [(float(a), float(b)) for a, b in
             re.findall(r"black_start:(-?[\d.]+)\s+black_end:(-?[\d.]+)", log)]
    starts = [float(x) for x in re.findall(r"freeze_start:\s*(-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*(-?[\d.]+)", log)]
    freezes = [(s, ends[i] if i < len(ends) else None) for i, s in enumerate(starts)]
    summary = log.rsplit("Summary:", 1)[-1] if "Summary:" in log else ""
    integrated = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", summary)
    peak = re.search(r"Peak:\s*(-?[\d.]+)\s*dBFS", summary)
    return {"returncode": result.returncode, "decode_errors": errors[:20], "black": black,
            "freezes": freezes,
            "integrated_lufs": float(integrated.group(1)) if integrated else None,
            "true_peak_dbtp": float(peak.group(1)) if peak else None}


def delivery_qc(path: str | Path, contract: DeliveryContract, *,
                opening: Optional[str | Path] = None,
                body: Optional[str | Path] = None) -> dict[str, Any]:
    """Probe and fully decode a finished master; every mismatch is a blocker.

    Returns ``{"passed", "blockers", "checks", "measurements", "contract"}``
    in the shape of ``render_report.metadata.qc.technical``.
    """
    path = Path(path)
    info = probe(path)
    checks: list[dict[str, Any]] = []

    def check(name: str, expected: Any, actual: Any, passed: bool) -> None:
        checks.append({"check": name, "expected": expected, "actual": actual,
                       "passed": bool(passed)})

    videos, audios = _streams(info, "video"), _streams(info, "audio")
    check("video_streams", 1, len(videos), len(videos) == 1)
    check("audio_streams", 1, len(audios), len(audios) == 1)
    if videos:
        facts = _video_facts(videos[0])
        for key, want in _expected_video(contract).items():
            got = facts[key]
            check(key, want, got, _same_fps(got, want) if key == "fps" else got == want)
    if audios:
        audio = audios[0]
        check("audio_codec", contract.audio_codec, audio.get("codec_name"),
              audio.get("codec_name") == contract.audio_codec)
        check("sample_rate", contract.sample_rate, int(audio.get("sample_rate", 0)),
              int(audio.get("sample_rate", 0)) == contract.sample_rate)
        check("channels", contract.channels, audio.get("channels"),
              audio.get("channels") == contract.channels)

    duration = float((info.get("format") or {}).get("duration") or 0.0)
    if contract.duration_seconds is not None:
        check("duration_seconds", contract.duration_seconds, round(duration, 3),
              abs(duration - contract.duration_seconds) <= contract.duration_tolerance_seconds)
    if videos and audios:
        vd = float(videos[0].get("duration") or duration)
        ad = float(audios[0].get("duration") or duration)
        vs = float(videos[0].get("start_time") or 0.0)
        as_ = float(audios[0].get("start_time") or 0.0)
        check("av_duration_difference_seconds", f"<= {contract.av_duration_tolerance_seconds}",
              round(abs(vd - ad), 3), abs(vd - ad) <= contract.av_duration_tolerance_seconds)
        check("av_start_offset_seconds", f"<= {contract.av_duration_tolerance_seconds}",
              round(abs(vs - as_), 3), abs(vs - as_) <= contract.av_duration_tolerance_seconds)

    scan = _decode_scan(path)
    check("clean_decode", "no decoder errors", scan["decode_errors"] or "none",
          scan["returncode"] == 0 and not scan["decode_errors"])
    allowance = contract.black_allowance_seconds
    bad_black = [(a, b) for a, b in scan["black"]
                 if b - a >= contract.black_min_seconds
                 and not (b <= allowance or a >= duration - allowance)]
    check("black_frames", f"none >= {contract.black_min_seconds}s outside the first/last "
          f"{allowance}s", bad_black or "none", not bad_black)
    bad_freeze = [(s, e) for s, e in scan["freezes"]
                  if ((e if e is not None else duration) - s) >= contract.freeze_min_seconds]
    check("frozen_frames", f"none >= {contract.freeze_min_seconds}s", bad_freeze or "none",
          not bad_freeze)
    lufs, tp = scan["integrated_lufs"], scan["true_peak_dbtp"]
    check("integrated_lufs", f"{contract.integrated_lufs} +/- {contract.loudness_tolerance_lu}",
          lufs, lufs is not None
          and abs(lufs - contract.integrated_lufs) <= contract.loudness_tolerance_lu)
    check("true_peak_dbtp", f"<= {contract.true_peak_max_dbtp}", tp,
          tp is not None and tp <= contract.true_peak_max_dbtp)

    if opening is not None and body is not None:
        mismatches = compare_segments(opening, body, contract)
        check("opening_body_compatible", "identical format and colour", mismatches or "identical",
              not mismatches)

    blockers = [f"{c['check']}: expected {c['expected']}, got {c['actual']}"
                for c in checks if not c["passed"]]
    return {"passed": not blockers, "blockers": blockers, "checks": checks,
            "measurements": {"duration_seconds": round(duration, 3), **{
                k: scan[k] for k in ("integrated_lufs", "true_peak_dbtp")}},
            "contract": contract.to_dict(), "file": str(path)}
