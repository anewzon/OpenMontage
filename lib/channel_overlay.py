"""Channel-owned persistent video overlays: declared once, composited per edit.

A channel may own a reusable overlay - a subscribe animation, a logo, a small
corner bug, another CTA - and declare it in the ``overlays:`` list of its
BRAND.md ``channel-policy`` block. The pipeline knows nothing about any
particular overlay: no filename, timing or styling lives in generic code. A
channel that declares none behaves exactly as before.

The path, in order, all channel-agnostic:

1. `resolve_overlays` - the channel's declarations, each resolved to a file
   under the channel folder and INSPECTED with ffprobe (`inspect_media`): real
   dimensions, frame rate, codec, pixel format, whether the alpha plane
   actually varies (a ``.mov`` is not transparency; ``yuva`` is a promise the
   plane must keep), colour tags and any audio streams. A declared overlay
   that is missing or unusable is an error here - production stops and says
   so; it never silently omits the overlay.
2. `schedule_overlay` - the edit chooses WHEN from the episode itself: the
   timeline's slots, their shot scale and subject motion, the opening and the
   final fade. No global timestamp. The decision is recorded in
   ``edit_decisions.metadata.overlays[]`` with the asset's SHA-256, so the
   composition is deterministic afterwards.
3. `composite_overlay` - burns the recorded overlay into ONE piece (the chunk
   that contains its window) at the delivery contract, proportions preserved,
   the overlay's own audio never mapped. It measures its own effect (frame
   difference inside and outside the window) and records that evidence.
4. `overlay_qc` - re-checks the delivered file against the record: window
   inside the runtime, region inside the frame, aspect preserved, evidence
   present, exactly one audio stream. The existing `delivery_qc` still runs on
   the master; this adds to it, never replaces it.

Provenance: a channel asset is reusable, so its evidence is reusable too. It
lives once, beside the asset, in ``brand_assets/PROVENANCE.md`` (one fenced
``channel-provenance`` block, see `channel_asset_provenance`); the publish
gate reads it for every overlay the edit uses instead of asking each project
for a copy of the same receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from lib.ffmpeg_runtime import ffmpeg_path, ffprobe_path

__all__ = [
    "OverlayError", "OverlayPolicy", "OverlayMedia", "ResolvedOverlay",
    "overlay_policies_from_data", "inspect_media", "resolve_overlays", "schedule_overlay",
    "overlay_filter_graph", "composite_overlay", "overlay_qc", "channel_asset_provenance",
    "provenance_for", "PROVENANCE_FILE",
]

PROVENANCE_FILE = "brand_assets/PROVENANCE.md"
_PROVENANCE_BLOCK = re.compile(r"^```channel-provenance[ \t]*\r?\n(.*?)^```[ \t]*\r?$",
                               re.S | re.M)

OVERLAY_KEYS = {"id", "enabled", "asset", "purpose", "usage", "placement", "keep_out",
                "preserve_proportions", "alter", "audio", "scale"}
OVERLAY_REQUIRED = {"id", "enabled", "asset", "purpose", "usage"}
USAGES = ("once_per_video", "every_movement", "continuous")
PLACEMENTS = ("as_authored", "centre", "top_left", "top_right", "bottom_left", "bottom_right")
KEEP_OUT = ("opening", "main_subject", "final_fade")
ALTER = ("none", "recolour_allowed", "resize_allowed")
AUDIO = ("exclude", "include")
#: Margin from the frame edge for corner placements, as a fraction of frame width.
CORNER_MARGIN = 0.03
#: A frame difference (0-255 mean absolute) this large inside the window and
#: this much larger than outside it is the overlay actually being there.
EVIDENCE_MIN_INSIDE = 1.0
EVIDENCE_RATIO = 3.0
#: The final seconds of a piece are its fade; an overlay ending inside them is
#: fading with it, which reads as a defect.
FINAL_FADE_SECONDS = 5.0


class OverlayError(ValueError):
    """A declared overlay is missing, unusable, unschedulable or wrongly composited."""


# --------------------------------------------------------------------------
# 1. Declaration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlayPolicy:
    """One overlay as the channel declares it. Nothing here is a filename the pipeline chose."""

    id: str
    enabled: bool
    asset: str                          # relative to the channel folder
    purpose: str
    usage: str                          # once_per_video | every_movement | continuous
    placement: str = "as_authored"      # where; the edit may narrow, never widen
    keep_out: tuple[str, ...] = ("opening", "main_subject", "final_fade")
    preserve_proportions: bool = True
    alter: str = "none"                 # normal production never recolours/redesigns
    audio: str = "exclude"              # the asset's own audio never enters the mix
    scale: Optional[float] = None       # fraction of frame width for a non-full-frame asset

    def to_metadata(self) -> dict[str, Any]:
        return {"id": self.id, "enabled": self.enabled, "asset": self.asset,
                "purpose": self.purpose, "usage": self.usage, "placement": self.placement,
                "keep_out": list(self.keep_out), "preserve_proportions": self.preserve_proportions,
                "alter": self.alter, "audio": self.audio, "scale": self.scale}


def _fail(msg: str) -> None:
    raise OverlayError(msg)


def overlay_policies_from_data(data: Any) -> tuple[OverlayPolicy, ...]:
    """Validate the ``overlays:`` list of a channel-policy block (absent -> none)."""
    if data is None:
        return ()
    if not isinstance(data, list):
        _fail("channel-policy.overlays must be a list")
    out: list[OverlayPolicy] = []
    for i, item in enumerate(data):
        where = f"channel-policy.overlays[{i}]"
        if not isinstance(item, Mapping):
            _fail(f"{where} must be a mapping")
        unknown = set(item) - OVERLAY_KEYS
        if unknown:
            _fail(f"{where} has unknown key(s) {sorted(unknown)}")
        missing = OVERLAY_REQUIRED - set(item)
        if missing:
            _fail(f"{where} is missing required key(s) {sorted(missing)}")
        for key in ("id", "asset", "purpose"):
            if not isinstance(item[key], str) or not item[key].strip():
                _fail(f"{where}.{key} must be non-empty text")
        if not isinstance(item["enabled"], bool):
            _fail(f"{where}.enabled must be true or false")
        asset = item["asset"].replace("\\", "/").strip()
        if asset.startswith(("/", "..")) or ":" in asset or "/../" in asset:
            _fail(f"{where}.asset must be a path relative to the channel folder, got {asset!r}")
        usage = str(item["usage"]).lower()
        if usage not in USAGES:
            _fail(f"{where}.usage must be one of {list(USAGES)}")
        placement = str(item.get("placement", "as_authored")).lower()
        if placement not in PLACEMENTS:
            _fail(f"{where}.placement must be one of {list(PLACEMENTS)}")
        keep_out = item.get("keep_out", list(KEEP_OUT))
        if not isinstance(keep_out, list) or any(k not in KEEP_OUT for k in keep_out):
            _fail(f"{where}.keep_out must be a list from {list(KEEP_OUT)}")
        alter = str(item.get("alter", "none")).lower()
        if alter not in ALTER:
            _fail(f"{where}.alter must be one of {list(ALTER)}")
        audio = str(item.get("audio", "exclude")).lower()
        if audio not in AUDIO:
            _fail(f"{where}.audio must be one of {list(AUDIO)}")
        preserve = item.get("preserve_proportions", True)
        if not isinstance(preserve, bool):
            _fail(f"{where}.preserve_proportions must be true or false")
        scale = item.get("scale")
        if scale is not None and (isinstance(scale, bool) or not isinstance(scale, (int, float))
                                  or not 0 < float(scale) <= 1):
            _fail(f"{where}.scale must be a fraction of the frame width in (0, 1]")
        out.append(OverlayPolicy(
            id=item["id"].strip(), enabled=item["enabled"], asset=asset,
            purpose=item["purpose"].strip(), usage=usage, placement=placement,
            keep_out=tuple(keep_out), preserve_proportions=preserve, alter=alter, audio=audio,
            scale=None if scale is None else float(scale)))
    ids = [o.id for o in out]
    if len(set(ids)) != len(ids):
        _fail(f"channel-policy.overlays ids must be unique, got {ids}")
    return tuple(out)


# --------------------------------------------------------------------------
# 2. Inspection: what the file actually is
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlayMedia:
    path: str
    sha256: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    fps: float
    codec: str
    pix_fmt: str
    has_alpha_plane: bool           # the pixel format carries an alpha plane
    alpha_varies: bool              # ...and the plane is not simply opaque everywhere
    color_range: Optional[str]
    color_space: Optional[str]
    audio_streams: int
    data_streams: int

    def to_metadata(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _alpha_varies(path: Path, duration: float) -> bool:
    """True when the alpha plane is not uniformly opaque across sampled frames.

    Samples a few frames spread over the duration; a plane whose average is
    at its maximum on every sample is opaque, whatever the container claims.
    """
    times = [duration * f for f in (0.05, 0.3, 0.5, 0.7, 0.95)] if duration > 0.5 else [0.0]
    averages = []
    for t in times:
        result = _run([ffmpeg_path(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
                       "-frames:v", "1", "-vf",
                       "alphaextract,format=gray,signalstats,"
                       "metadata=print:key=lavfi.signalstats.YAVG:file=-",
                       "-f", "null", "-"])
        for line in result.stdout.splitlines():
            if "YAVG=" in line:
                try:
                    averages.append(float(line.split("YAVG=")[1]))
                except ValueError:
                    pass
                break
    if not averages:
        return False
    # 8-bit gray after format=gray: 255 is fully opaque.
    return any(a < 250.0 for a in averages)


def inspect_media(path: str | Path) -> OverlayMedia:
    """ffprobe the file and MEASURE its alpha; raise `OverlayError` when unusable."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        _fail(f"overlay asset {path} is missing or empty")
    result = _run([ffprobe_path(), "-v", "error", "-show_streams", "-show_format",
                   "-of", "json", str(path)])
    if result.returncode != 0:
        _fail(f"overlay asset {path.name} cannot be probed: {result.stderr.strip()[:200]}")
    info = json.loads(result.stdout or "{}")
    streams = info.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    if not videos:
        _fail(f"overlay asset {path.name} has no video stream")
    v = videos[0]
    try:
        num, den = str(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except ValueError:
        fps = 0.0
    duration = float(v.get("duration") or (info.get("format") or {}).get("duration") or 0.0)
    width, height = int(v.get("width") or 0), int(v.get("height") or 0)
    if width <= 0 or height <= 0 or duration <= 0 or fps <= 0:
        _fail(f"overlay asset {path.name} has no usable dimensions, duration or frame rate "
              f"({width}x{height}, {duration}s, {fps} fps)")
    pix = str(v.get("pix_fmt") or "")
    has_alpha = bool(re.match(r"^(yuva|rgba|argb|abgr|bgra|gbrap|ya|pal8)", pix))
    return OverlayMedia(
        path=str(path), sha256=_sha256(path), size_bytes=path.stat().st_size,
        duration_seconds=round(duration, 3), width=width, height=height, fps=round(fps, 3),
        codec=str(v.get("codec_name") or ""), pix_fmt=pix, has_alpha_plane=has_alpha,
        alpha_varies=_alpha_varies(path, duration) if has_alpha else False,
        color_range=v.get("color_range"), color_space=v.get("color_space"),
        audio_streams=sum(1 for s in streams if s.get("codec_type") == "audio"),
        data_streams=sum(1 for s in streams if s.get("codec_type") == "data"))


@dataclass(frozen=True)
class ResolvedOverlay:
    policy: OverlayPolicy
    media: OverlayMedia
    channel_root: str

    def to_metadata(self) -> dict[str, Any]:
        return {"policy": self.policy.to_metadata(), "media": self.media.to_metadata(),
                "channel_root": self.channel_root}


def resolve_overlays(policies: Iterable[OverlayPolicy], channel_root: str | Path,
                     *, frame: Optional[tuple[int, int]] = None) -> list[ResolvedOverlay]:
    """Resolve every ENABLED declaration to an inspected file; any problem is an error.

    ``frame`` (delivery width, height) lets a full-frame asset be checked for
    a compatible aspect: an asset authored on a canvas of another shape
    cannot keep its proportions and fill the frame at once.
    """
    root = Path(channel_root)
    resolved = []
    for policy in policies:
        if not policy.enabled:
            continue
        path = (root / policy.asset).resolve()
        if root.resolve() not in path.parents:
            _fail(f"overlay {policy.id!r}: asset {policy.asset!r} escapes the channel folder")
        if not path.is_file():
            _fail(f"overlay {policy.id!r}: declared asset {policy.asset!r} is not present under "
                  f"{root} - production stops; the overlay is never silently omitted")
        media = inspect_media(path)
        if policy.placement == "as_authored" and frame and policy.preserve_proportions:
            fw, fh = frame
            if abs(media.width / media.height - fw / fh) > 0.01:
                _fail(f"overlay {policy.id!r}: authored on a {media.width}x{media.height} canvas, "
                      f"which cannot fill a {fw}x{fh} frame with its proportions preserved; "
                      "declare a corner/centre placement with a scale instead")
        if policy.audio == "exclude" and media.audio_streams:
            # Allowed, but recorded: the audio is never mapped (see composite_overlay).
            pass
        resolved.append(ResolvedOverlay(policy=policy, media=media, channel_root=str(root)))
    return resolved


# --------------------------------------------------------------------------
# 3. Scheduling: the edit chooses the moment
# --------------------------------------------------------------------------


_SCALE_SCORE = {"wide": 3, "medium": 2, "detail": 0, "close": 0}
_MOTION_SCORE = {"still": 3, "gentle": 3, "moderate": 1, "strong": 0}


def _region(policy: OverlayPolicy, media: OverlayMedia, frame: tuple[int, int]) -> dict[str, int]:
    fw, fh = frame
    if policy.placement == "as_authored":
        # Full-frame asset: scaled to the frame, proportions already verified.
        return {"x": 0, "y": 0, "w": fw, "h": fh}
    fraction = policy.scale or min(0.25, media.width / fw)
    w = max(1, int(round(fw * fraction)))
    h = max(1, int(round(w * media.height / media.width))) if policy.preserve_proportions \
        else max(1, int(round(fh * fraction)))
    if h > fh:
        h = fh
        w = max(1, int(round(h * media.width / media.height)))
    margin = int(round(fw * CORNER_MARGIN))
    x = {"top_left": margin, "bottom_left": margin, "top_right": fw - w - margin,
         "bottom_right": fw - w - margin, "centre": (fw - w) // 2}[policy.placement]
    y = {"top_left": margin, "top_right": margin, "bottom_left": fh - h - margin,
         "bottom_right": fh - h - margin, "centre": (fh - h) // 2}[policy.placement]
    return {"x": max(0, x), "y": max(0, y), "w": w, "h": h}


def schedule_overlay(resolved: ResolvedOverlay, *, runtime_seconds: float,
                     opening_seconds: float, slots: Iterable[Mapping[str, Any]],
                     frame: tuple[int, int], placement: Optional[str] = None,
                     final_fade_seconds: float = FINAL_FADE_SECONDS) -> dict[str, Any]:
    """Choose WHEN and WHERE this overlay appears in this episode, and record why.

    ``slots`` are the timeline's shots as the scene plan / edit knows them:
    ``start_seconds``, ``end_seconds`` and, where available, ``shot_scale``
    and ``subject_motion``. The overlay is placed inside one slot when a slot
    is long enough (no cut under it), preferring wide or medium shots with
    still or gentle subject motion, in the second quarter of the runtime,
    never over the opening or the final fade. When no slot can hold it (a
    short piece), the earliest window after the opening that still ends
    before the fade is used; when even that fails, the piece is too short and
    that is an error, not a silent omission.
    """
    policy, media = resolved.policy, resolved.media
    placement = (placement or policy.placement).lower()
    if placement not in PLACEMENTS:
        _fail(f"placement {placement!r} is not one of {list(PLACEMENTS)}")
    if placement != policy.placement and policy.placement != "as_authored":
        # The edit may move a corner bug to another corner; it may not turn a
        # full-frame authored piece into a corner bug, or the reverse.
        pass
    elif placement != policy.placement:
        _fail(f"overlay {policy.id!r} is authored full-frame; the edit cannot re-place it")
    dur = media.duration_seconds
    earliest = opening_seconds + 1.0 if "opening" in policy.keep_out else 0.0
    latest_end = runtime_seconds - (final_fade_seconds if "final_fade" in policy.keep_out else 0.0)
    if latest_end - earliest < dur:
        _fail(f"overlay {policy.id!r} ({dur:.1f}s) cannot fit between the opening and the final "
              f"fade of a {runtime_seconds:.1f}s piece")
    sweet_low, sweet_high = runtime_seconds * 0.2, runtime_seconds * 0.6
    candidates = []
    for slot in slots:
        s, e = float(slot.get("start_seconds", 0)), float(slot.get("end_seconds", 0))
        start = max(s, earliest)
        if e - start < dur or start + dur > latest_end:
            continue
        score = _SCALE_SCORE.get(str(slot.get("shot_scale", "")).lower(), 1)
        score += _MOTION_SCORE.get(str(slot.get("subject_motion", "")).lower(), 1)
        if sweet_low <= start <= sweet_high:
            score += 2
        # Start a little into the shot so the overlay never rides a cut.
        settle = min(1.0, max(0.0, (e - start - dur) / 2))
        candidates.append((-score, start + settle, slot.get("id"), slot))
    if candidates:
        candidates.sort(key=lambda c: (c[0], c[1]))
        _, start, slot_id, slot = candidates[0]
        rationale = (f"inside shot {slot_id} ({slot.get('shot_scale', '?')}, subject "
                     f"{slot.get('subject_motion', '?')}), clear of the opening and the fade; "
                     "no cut under the overlay")
    else:
        start, slot_id = earliest, None
        rationale = ("no single shot is long enough to hold the overlay; earliest window after "
                     "the opening that ends before the final fade (short piece)")
    start = round(start, 3)
    region = _region(policy, media, frame)
    return {
        "id": policy.id, "purpose": policy.purpose, "asset": policy.asset,
        "asset_sha256": media.sha256, "channel_root": resolved.channel_root,
        "start_seconds": start, "end_seconds": round(start + dur, 3),
        "duration_seconds": dur, "placement": placement, "region": region,
        "frame": {"width": frame[0], "height": frame[1]},
        "preserve_proportions": policy.preserve_proportions,
        "audio": "excluded" if policy.audio == "exclude" else "included",
        "source_audio_streams": media.audio_streams,
        "alpha": {"plane": media.has_alpha_plane, "varies": media.alpha_varies},
        "slot_id": slot_id, "rationale": rationale, "policy": policy.to_metadata(),
    }


# --------------------------------------------------------------------------
# 4. Compositing: deterministic from the record
# --------------------------------------------------------------------------


def overlay_filter_graph(record: Mapping[str, Any], media: OverlayMedia, *, body_input: int = 0,
                         overlay_input: int = 1, piece_offset_seconds: float = 0.0) -> str:
    """The filter_complex that burns one recorded overlay into a piece.

    ``piece_offset_seconds`` is where the piece starts on the programme
    timeline (a chunk that begins at 600 s composites a 620 s overlay at 20 s).
    The overlay is scaled to its recorded region (proportions were fixed when
    the region was chosen), delayed to its start, and blended only inside its
    window; the piece's own pixels are untouched everywhere else.
    """
    start = float(record["start_seconds"]) - piece_offset_seconds
    end = float(record["end_seconds"]) - piece_offset_seconds
    r = record["region"]
    alpha_fmt = "yuva420p" if media.has_alpha_plane else "yuv420p"
    return (f"[{overlay_input}:v]format={alpha_fmt},scale={r['w']}:{r['h']}:flags=lanczos,"
            f"setpts=PTS-STARTPTS+{start:.3f}/TB[ov];"
            f"[{body_input}:v][ov]overlay=x={r['x']}:y={r['y']}:eof_action=pass:"
            f"enable='between(t,{start:.3f},{end:.3f})'[v]")


def _mean_frame_diff(a: Path, b: Path, t: float, width: int, height: int) -> Optional[float]:
    """Mean absolute luma difference (0-255) between two files at time ``t``."""
    import numpy as np

    frames = []
    for path in (a, b):
        result = subprocess.run(
            [ffmpeg_path(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True)
        if result.returncode != 0 or len(result.stdout) < width * height:
            return None
        frames.append(np.frombuffer(result.stdout[: width * height], dtype=np.uint8)
                      .astype(np.int16))
    return float(np.abs(frames[0] - frames[1]).mean())


def composite_overlay(source: str | Path, output: str | Path, record: Mapping[str, Any],
                      contract: Any, *, piece_offset_seconds: float = 0.0) -> dict[str, Any]:
    """Burn a recorded overlay into a piece, at the delivery contract, and measure it.

    The overlay's own audio is never mapped: only the piece's audio (if any)
    passes through. Returns the record extended with ``evidence``: the frame
    difference against the source inside and just outside the window, which
    `overlay_qc` later requires. Raises `OverlayError` when the asset on disk
    is not the one the record names (hash) or the composite fails.
    """
    from lib.delivery_qc import DeliveryContractError, _stamp, encode_args, probe

    source, output = Path(source), Path(output)
    asset = Path(record["channel_root"]) / record["asset"]
    if not asset.is_file():
        _fail(f"overlay {record['id']!r}: asset {asset} is missing at composite time")
    if _sha256(asset) != record["asset_sha256"]:
        _fail(f"overlay {record['id']!r}: asset {asset.name} changed since the edit recorded it")
    media = inspect_media(asset)
    fw, fh = record["frame"]["width"], record["frame"]["height"]
    if (fw, fh) != (contract.width, contract.height):
        _fail(f"overlay {record['id']!r} was scheduled for a {fw}x{fh} frame; the contract is "
              f"{contract.width}x{contract.height}")
    graph = overlay_filter_graph(record, media, piece_offset_seconds=piece_offset_seconds)
    graph = graph.replace("[v]", f",{_stamp(contract)}[v]")
    has_audio = bool([s for s in probe(source).get("streams", [])
                      if s.get("codec_type") == "audio"])
    args = [ffmpeg_path(), "-y", "-v", "error", "-i", str(source), "-i", str(asset),
            "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        args += ["-map", "0:a:0"]
    args += encode_args(contract, audio=has_audio) + [str(output)]
    result = _run(args)
    if result.returncode != 0:
        raise DeliveryContractError(f"overlay composite failed: {result.stderr.strip()[:400]}")
    start = float(record["start_seconds"]) - piece_offset_seconds
    end = float(record["end_seconds"]) - piece_offset_seconds
    mid = (start + end) / 2
    before = max(0.0, start - 0.5)
    inside = _mean_frame_diff(source, output, mid, 320, 180)
    outside = _mean_frame_diff(source, output, before, 320, 180)
    out_probe = probe(output)
    audio_out = [s for s in out_probe.get("streams", []) if s.get("codec_type") == "audio"]
    extended = dict(record)
    extended["evidence"] = {
        "piece": str(output), "piece_offset_seconds": piece_offset_seconds,
        "frame_diff_inside_window": None if inside is None else round(inside, 3),
        "frame_diff_outside_window": None if outside is None else round(outside, 3),
        "output_audio_streams": len(audio_out), "overlay_audio_mapped": False,
        "filter_graph": graph,
    }
    return extended


# --------------------------------------------------------------------------
# 5. QC
# --------------------------------------------------------------------------


def overlay_qc(final: str | Path, records: Iterable[Mapping[str, Any]], contract: Any,
               *, expected_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Checks that every recorded overlay is in the delivered file as declared.

    Blockers: a declared overlay with no record (silently omitted); a record
    with no composite evidence; evidence that shows no visible effect; a
    window outside the runtime; a region outside the frame; proportions not
    preserved; the overlay's audio mapped; more than one audio stream in the
    file. Channels with no overlays get an empty, passing report.
    """
    from lib.delivery_qc import probe

    records = list(records)
    blockers: list[str] = []
    checks: list[dict[str, Any]] = []
    info = probe(final)
    fmt = info.get("format") or {}
    runtime = float(fmt.get("duration") or 0.0)
    audio_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if len(audio_streams) != 1:
        blockers.append(f"delivered file has {len(audio_streams)} audio streams, expected 1")
    recorded_ids = {r.get("id") for r in records}
    for expected in expected_ids:
        if expected not in recorded_ids:
            blockers.append(f"overlay {expected!r} is declared by the channel but has no record "
                            "in the edit - it was omitted, not composited")
    for r in records:
        rid = r.get("id")
        ev = r.get("evidence") or {}
        s, e = float(r.get("start_seconds", -1)), float(r.get("end_seconds", -1))
        if not (0 <= s < e <= runtime + 0.05):
            blockers.append(f"overlay {rid!r}: window {s}-{e}s is outside the {runtime:.2f}s file")
        reg = r.get("region") or {}
        fw, fh = contract.width, contract.height
        inside = (0 <= reg.get("x", -1) and 0 <= reg.get("y", -1)
                  and reg.get("x", 0) + reg.get("w", 0) <= fw
                  and reg.get("y", 0) + reg.get("h", 0) <= fh)
        if not inside:
            blockers.append(f"overlay {rid!r}: region {reg} is outside the {fw}x{fh} frame")
        if r.get("preserve_proportions", True):
            root = Path(r.get("channel_root", "")) / str(r.get("asset", ""))
            if root.is_file():
                media = inspect_media(root)
                if reg.get("w") and reg.get("h") and \
                        abs(reg["w"] / reg["h"] - media.width / media.height) > 0.02:
                    blockers.append(f"overlay {rid!r}: region {reg['w']}x{reg['h']} does not "
                                    f"preserve the asset's {media.width}x{media.height} "
                                    "proportions")
            else:
                blockers.append(f"overlay {rid!r}: asset {root} is not present for QC")
        if not ev:
            blockers.append(f"overlay {rid!r}: no composite evidence - it was scheduled but "
                            "never composited")
        else:
            i, o = ev.get("frame_diff_inside_window"), ev.get("frame_diff_outside_window")
            if i is None or o is None:
                blockers.append(f"overlay {rid!r}: composite evidence could not be measured")
            elif not (i >= EVIDENCE_MIN_INSIDE and i >= EVIDENCE_RATIO * max(o, 0.05)):
                blockers.append(f"overlay {rid!r}: no visible effect inside its window "
                                f"(diff {i} inside vs {o} outside)")
            if ev.get("overlay_audio_mapped"):
                blockers.append(f"overlay {rid!r}: its audio was mapped into the programme")
        if r.get("audio") == "excluded" and r.get("source_audio_streams", 0) and \
                (ev.get("overlay_audio_mapped") is not False):
            blockers.append(f"overlay {rid!r}: asset carries audio and exclusion is unproven")
        checks.append({"id": rid, "window": [s, e], "region": reg, "evidence": ev})
    return {"passed": not blockers, "blockers": blockers, "overlays": checks,
            "runtime_seconds": runtime}


# --------------------------------------------------------------------------
# 6. Reusable provenance for channel assets
# --------------------------------------------------------------------------


def channel_asset_provenance(channel_root: str | Path) -> dict[str, dict[str, Any]]:
    """The channel's asset provenance, keyed by asset path relative to the channel folder.

    Read from ``brand_assets/PROVENANCE.md``: one fenced ``channel-provenance``
    YAML block, a mapping of asset path -> {licence, source, verified_by,
    date, receipt (optional, relative to brand_assets), sha256 (optional)}.
    Absent file -> empty mapping (nothing is assumed).
    """
    import yaml

    path = Path(channel_root) / PROVENANCE_FILE
    if not path.is_file():
        return {}
    blocks = _PROVENANCE_BLOCK.findall(path.read_text(encoding="utf-8"))
    if len(blocks) != 1:
        _fail(f"{path} must contain exactly one ```channel-provenance block; found {len(blocks)}")
    try:
        data = yaml.safe_load(blocks[0]) or {}
    except yaml.YAMLError as exc:
        _fail(f"{path}: channel-provenance block is not valid YAML: {exc}")
    if not isinstance(data, Mapping):
        _fail(f"{path}: channel-provenance must map asset paths to evidence")
    out = {}
    for key, value in data.items():
        if not isinstance(value, Mapping):
            _fail(f"{path}: evidence for {key!r} must be a mapping")
        out[str(key).replace("\\", "/")] = {str(k): (v.isoformat() if hasattr(v, "isoformat")
                                                     else v) for k, v in value.items()}
    return out


def provenance_for(channel_root: str | Path, asset_rel: str) -> tuple[Optional[str], Optional[str]]:
    """``(verified description, None)`` or ``(None, why it is not verified)`` for one asset."""
    root = Path(channel_root)
    rel = asset_rel.replace("\\", "/")
    try:
        entries = channel_asset_provenance(root)
    except OverlayError as exc:
        return None, str(exc)
    # Entries are keyed relative to brand_assets/ (where PROVENANCE.md lives);
    # the full channel-relative path is accepted too.
    short = rel[len("brand_assets/"):] if rel.startswith("brand_assets/") else rel
    entry = entries.get(short) or entries.get(rel)
    if not entry:
        return None, (f"channel asset {rel!r} has no provenance evidence in "
                      f"{PROVENANCE_FILE} (add an entry: licence, source, verified_by, date)")
    for key in ("licence", "source", "verified_by", "date"):
        if not str(entry.get(key) or "").strip():
            return None, f"channel asset {rel!r}: provenance entry lacks {key!r}"
    asset = root / rel
    if not asset.is_file():
        return None, f"channel asset {rel!r} is missing"
    recorded_sha = entry.get("sha256")
    if (recorded_sha is not None and str(recorded_sha).strip()
            and _sha256(asset) != str(recorded_sha).strip().lower()):
        return None, f"channel asset {rel!r} changed since its provenance was recorded"
    receipt = entry.get("receipt")
    if receipt:
        receipt_path = root / "brand_assets" / str(receipt)
        if not receipt_path.is_file() or receipt_path.stat().st_size == 0:
            return None, f"channel asset {rel!r}: receipt {receipt!r} is missing or empty"
    return f"{entry['licence']} - {entry['source']} (verified by {entry['verified_by']}, " \
           f"{entry['date']})", None
