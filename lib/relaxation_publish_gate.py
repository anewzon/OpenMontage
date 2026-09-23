"""The relaxation publish gate: licence, provenance, mix and technical QC as BLOCKERS.

A project is publish-ready only when every asset the final edit actually uses
has verified evidence of the right to use it, the executed mix is the one the
channel's BRAND.md block produces, and the delivered master passes the
delivery contract. On channel_0001__video_0003 the missing Envato receipts
were a note in ``asset_manifest.metadata.licence_status`` and nothing stopped
at it; here they stop the gate.

Evidence by provenance:

* **licensed stock** (``source_tool`` starting ``licensed_manual``): a receipt
  file under the project's ``licenses/`` folder, recorded with its SHA-256 in
  ``asset_manifest.metadata.licence_evidence[<asset id>]`` and unchanged since
  (`record_licence_evidence`);
* **free stock** from a provider tool: the licence name and the item's
  ``original_url`` on the asset;
* **generated audio**: an accepted candidate in the paid music ledger, or a
  completed call in the paid SFX ledger - the Phase 1 records of what was
  paid for;
* anything else, or media the edit uses that the manifest does not list:
  unknown provenance, a blocker.

Unused downloaded alternates are listed separately and never block.

`assess_publish_readiness` returns the report; `write_publish_gate` writes the
publish checkpoint from it - ``failed`` with every blocker while any remains,
``awaiting_human`` (the operator's gate) only when there are none - and
`approve_publish` re-verifies the master has not changed before writing
``completed``. The core checkpoint writer is not modified; see the Publish
Director for why this is the gate to use.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

__all__ = [
    "PublishBlocked",
    "approve_publish",
    "assess_publish_readiness",
    "licence_report",
    "record_licence_evidence",
    "used_asset_references",
    "write_publish_gate",
]

#: Provider tools whose free-stock items carry their licence on the asset.
FREE_STOCK_TOOLS = frozenset({"pexels_video", "pixabay_video", "direct_clip_search",
                              "pixabay_music", "freesound_music", "pexels_image",
                              "pixabay_image"})
GENERATED_AUDIO_TOOLS = {"suno_music": "music", "elevenlabs_sfx": "sfx"}


class PublishBlocked(RuntimeError):
    """The project is not publish-ready; the message lists every blocker."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# What the final edit actually uses
# --------------------------------------------------------------------------


def used_asset_references(edit_decisions: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every media reference the edit uses: cuts, the opening bed, audio layers and beds."""
    refs: list[dict[str, str]] = []
    for cut in edit_decisions.get("cuts") or []:
        if cut.get("source"):
            refs.append({"ref": str(cut["source"]), "where": f"cut {cut.get('id')}"})
    meta = edit_decisions.get("metadata") or {}
    opening = meta.get("opening") or {}
    if opening.get("bed_asset_id"):
        refs.append({"ref": str(opening["bed_asset_id"]), "where": "opening bed"})
    for layer in meta.get("audio_layers") or []:
        if layer.get("asset_id"):
            refs.append({"ref": str(layer["asset_id"]), "where": f"audio {layer.get('role')}"})
        for bed in layer.get("beds") or []:
            ref = bed.get("asset_id") or bed.get("bed")
            if ref:
                refs.append({"ref": str(ref), "where": f"audio {layer.get('role')} bed"})
    return refs


def _resolve(ref: str, assets: list[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    for asset in assets:
        if asset.get("id") == ref:
            return asset
    target = Path(ref)
    for asset in assets:
        if asset.get("path") and Path(asset["path"]) == target:
            return asset
    return None


# --------------------------------------------------------------------------
# Licence and provenance
# --------------------------------------------------------------------------


def record_licence_evidence(project_dir: Path, asset_manifest: dict[str, Any], asset_id: str,
                            receipt: str | Path, *, licence: str, item: str,
                            verified_by: str) -> dict[str, Any]:
    """Record a licence receipt the operator placed in ``<project>/licenses/``.

    The receipt must be a non-empty file inside the project's licenses folder;
    its SHA-256 is recorded so a later swap or deletion is detected.
    """
    project_dir = Path(project_dir)
    licences = (project_dir / "licenses").resolve()
    path = Path(receipt)
    path = (project_dir / path).resolve() if not path.is_absolute() else path.resolve()
    if licences not in path.parents or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"licence evidence must be a non-empty file under {licences}")
    if not any(a.get("id") == asset_id for a in asset_manifest.get("assets") or []):
        raise ValueError(f"asset {asset_id!r} is not in the asset manifest")
    entry = {"file": path.relative_to(project_dir.resolve()).as_posix(),
             "sha256": _sha256(path), "licence": licence, "item": item,
             "verified_by": verified_by,
             "recorded_at": datetime.now(timezone.utc).isoformat()}
    asset_manifest.setdefault("metadata", {}).setdefault("licence_evidence", {})[asset_id] = entry
    return entry


def _ledger(project_dir: Path, name: str) -> Mapping[str, Any]:
    path = project_dir / "work" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _generated_provenance(project_dir: Path, asset: Mapping[str, Any]) -> Optional[str]:
    """None when a generated asset is a paid, accepted output on record; else why not."""
    kind = GENERATED_AUDIO_TOOLS[asset["source_tool"]]
    target = Path(asset.get("path", ""))
    if kind == "music":
        ledger = _ledger(project_dir, "paid_music_ledger.json")
        reviews: dict[tuple[int, int], str] = {}
        for review in ledger.get("reviews", []):
            for index, res in (review.get("resolutions") or {}).items():
                reviews[(int(review["request"]), int(index))] = res.get("outcome")
        for req in ledger.get("requests", []):
            for cand in req.get("candidates") or []:
                if cand.get("path") and Path(cand["path"]) == target:
                    outcome = reviews.get((int(req["request"]), int(cand["index"])),
                                          cand.get("outcome"))
                    return None if outcome == "accepted" else \
                        f"the music ledger records this candidate as {outcome}"
        return "no paid-music ledger record for this file"
    ledger = _ledger(project_dir, "paid_sfx_ledger.json")
    for call in ledger.get("calls", []):
        out = call.get("output") or call.get("output_path")
        if out and Path(out) == target:
            return None if call.get("status") == "completed" else \
                f"the SFX ledger records this call as {call.get('status')}"
    return "no paid-SFX ledger record for this file"


def licence_report(project_dir: Path, asset_manifest: Mapping[str, Any],
                   edit_decisions: Mapping[str, Any]) -> dict[str, Any]:
    """Blockers for every used asset without verified licence or provenance evidence."""
    project_dir = Path(project_dir)
    assets = list(asset_manifest.get("assets") or [])
    evidence = (asset_manifest.get("metadata") or {}).get("licence_evidence") or {}
    blockers: list[str] = []
    verified: dict[str, str] = {}
    used_ids: set[str] = set()
    for ref in used_asset_references(edit_decisions):
        asset = _resolve(ref["ref"], assets)
        if asset is None:
            blockers.append(f"{ref['where']} uses {ref['ref']!r}, which the asset manifest does "
                            "not list - its provenance is unknown")
            continue
        asset_id = str(asset["id"])
        if asset_id in used_ids:
            continue
        used_ids.add(asset_id)
        tool = str(asset.get("source_tool", ""))
        if tool.startswith("licensed_manual"):
            record = evidence.get(asset_id)
            if not record:
                blockers.append(f"{asset_id}: licensed stock used in the edit has no licence "
                                "evidence (asset_manifest.metadata.licence_evidence)")
                continue
            receipt = project_dir / str(record.get("file", ""))
            if not receipt.is_file() or receipt.stat().st_size == 0:
                blockers.append(f"{asset_id}: licence receipt {record.get('file')!r} is missing")
            elif _sha256(receipt) != record.get("sha256"):
                blockers.append(f"{asset_id}: licence receipt {record.get('file')!r} changed "
                                "since it was recorded")
            else:
                verified[asset_id] = f"receipt {record['file']}"
        elif tool in FREE_STOCK_TOOLS:
            url = str(asset.get("original_url") or "")
            if not asset.get("license") or not url.startswith(("http://", "https://")):
                blockers.append(f"{asset_id}: free stock without its licence and source URL")
            else:
                verified[asset_id] = f"{asset['license']} - {url}"
        elif tool in GENERATED_AUDIO_TOOLS:
            problem = _generated_provenance(project_dir, asset)
            if problem:
                blockers.append(f"{asset_id}: generated audio without paid provenance - {problem}")
            else:
                verified[asset_id] = f"{tool} ledger"
        else:
            blockers.append(f"{asset_id}: source_tool {tool!r} has no recognised provenance "
                            "evidence")
    unused = sorted(str(a["id"]) for a in assets if str(a["id"]) not in used_ids)
    return {"passed": not blockers, "blockers": blockers, "verified": verified,
            "used_assets": sorted(used_ids), "unused_alternates": unused}


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def assess_publish_readiness(
    project_dir: Path,
    *,
    proposal_packet: Mapping[str, Any],
    asset_manifest: Mapping[str, Any],
    edit_decisions: Mapping[str, Any],
    render_report: Mapping[str, Any],
    channel_brand: str | Path,
    final_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Every reason this project may not be published, with the evidence checked."""
    from lib.delivery_qc import DeliveryContract, DeliveryContractError, delivery_qc
    from lib.stem_balance import ChannelMixError, channel_mix_from_file, check_mix_record

    project_dir = Path(project_dir)
    final = Path(final_path) if final_path else project_dir / "output" / "final.mp4"
    blockers: list[str] = []

    licences = licence_report(project_dir, asset_manifest, edit_decisions)
    blockers += [f"licence: {b}" for b in licences["blockers"]]

    try:
        mix_blockers = check_mix_record(edit_decisions, channel_mix_from_file(channel_brand))
    except (ChannelMixError, OSError) as exc:
        mix_blockers = [f"the channel's mix block cannot be read: {exc}"]
    blockers += [f"mix: {b}" for b in mix_blockers]

    technical: dict[str, Any] = {"passed": False}
    final_sha = None
    if not final.is_file():
        blockers.append(f"technical: the master {final} does not exist")
    else:
        final_sha = _sha256(final)
        recorded = ((render_report.get("metadata") or {}).get("qc") or {}).get("technical") or {}
        try:
            contract = DeliveryContract.from_proposal(proposal_packet)
            opening = (edit_decisions.get("metadata") or {}).get("opening") or {}
            technical = delivery_qc(final, contract,
                                    opening=opening.get("rendered") if opening.get("rendered")
                                    and Path(opening["rendered"]).is_file() else None,
                                    body=(recorded.get("body") if recorded.get("body")
                                          and Path(recorded["body"]).is_file() else None))
        except DeliveryContractError as exc:
            technical = {"passed": False, "blockers": [str(exc)]}
        blockers += [f"technical: {b}" for b in technical.get("blockers", [])]
        if recorded and recorded.get("passed") is False:
            blockers.append("technical: the render report records a failed technical QC")

    return {
        "ready": not blockers, "blockers": blockers,
        "licence": licences, "mix_blockers": mix_blockers, "technical": technical,
        "final": str(final), "final_sha256": final_sha,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _publish_log(readiness: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {"version": "1.0",
            "entries": [{"platform": "youtube", "status": status,
                         "export_path": readiness["final"],
                         "timestamp": readiness["checked_at"]}],
            "metadata": {"publish_readiness": dict(readiness)}}


def write_publish_gate(pipeline_dir: Path, project_id: str,
                       readiness: Mapping[str, Any]) -> Path:
    """Write the publish checkpoint from a readiness report.

    Blockers -> ``failed`` with every blocker as the error (the project is not
    publish-ready and `get_next_stage` stays on publish). None ->
    ``awaiting_human``: the operator's own publish gate.
    """
    from lib.checkpoint import write_checkpoint

    if readiness["blockers"]:
        return write_checkpoint(
            Path(pipeline_dir), project_id, "publish", "failed",
            {"publish_log": _publish_log(readiness, "failed")},
            error="NOT PUBLISH-READY: " + " | ".join(readiness["blockers"]))
    return write_checkpoint(
        Path(pipeline_dir), project_id, "publish", "awaiting_human",
        {"publish_log": _publish_log(readiness, "pending_review")},
        human_approval_required=True)


def approve_publish(pipeline_dir: Path, project_id: str, *, operator_note: str,
                    **assess_kwargs: Any) -> Path:
    """Record the operator's publish approval - only for an unchanged, still-ready master.

    Re-runs `assess_publish_readiness`; raises `PublishBlocked` if anything now
    blocks or the master differs from the one presented for approval.
    """
    from lib.checkpoint import read_checkpoint, write_checkpoint

    current = read_checkpoint(Path(pipeline_dir), project_id, "publish") or {}
    presented = (((current.get("artifacts") or {}).get("publish_log") or {})
                 .get("metadata") or {}).get("publish_readiness") or {}
    if current.get("status") != "awaiting_human" or not presented.get("ready"):
        raise PublishBlocked("no publish-ready master is awaiting approval")
    readiness = assess_publish_readiness(Path(pipeline_dir) / project_id, **assess_kwargs)
    if readiness["blockers"]:
        raise PublishBlocked("; ".join(readiness["blockers"]))
    if readiness["final_sha256"] != presented.get("final_sha256"):
        raise PublishBlocked("the master changed after it was presented for approval")
    log = _publish_log(readiness, "exported")
    log["metadata"]["operator_approval"] = {
        "note": operator_note, "approved_at": datetime.now(timezone.utc).isoformat()}
    return write_checkpoint(Path(pipeline_dir), project_id, "publish", "completed",
                            {"publish_log": log}, human_approval_required=True,
                            human_approved=True)
