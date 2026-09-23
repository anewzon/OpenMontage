"""The publish gate blocks on missing licence or provenance evidence, the wrong mix
and a master that fails the delivery contract - and only then.

Licence receipts, ledgers and media are synthesised in a temporary project; no
production data, receipt or credential is used.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lib.checkpoint import get_completed_stages, read_checkpoint
from lib.relaxation_publish_gate import (
    assess_publish_readiness,
    licence_report,
    record_licence_evidence,
    used_asset_references,
    write_publish_gate,
)
from lib.stem_balance import channel_mix_from_file

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "tests" / "fixtures" / "relaxation" / "channels" / "reference_v7a_BRAND.md"


def asset(asset_id, tool, path, **extra):
    return {"id": asset_id, "type": extra.pop("type", "video"), "path": str(path),
            "source_tool": tool, "scene_id": "S1", **extra}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "projects"
    project = root / "channel_9999__video_0001"
    for sub in ("licenses", "assets/video", "assets/music", "work", "output"):
        (project / sub).mkdir(parents=True)
    (project / "project.json").write_text(json.dumps(
        {"version": "1.0", "project_id": project.name, "pipeline_type": "relaxation",
         "title": "fixture"}))
    return project


def manifest_and_edit(project):
    clip = project / "assets" / "video" / "river.mov"
    alt = project / "assets" / "video" / "alternate.mov"
    free = project / "assets" / "video" / "free.mp4"
    music = project / "assets" / "music" / "piece_g01.mp3"
    for p in (clip, alt, free, music):
        p.write_bytes(b"media")
    manifest = {"version": "1.0", "assets": [
        asset("RIVER1", "licensed_manual:envato", clip, provider="envato"),
        asset("ALT9", "licensed_manual:envato", alt, provider="envato"),
        asset("px_1", "pexels_video", free, license="Pexels License",
              original_url="https://www.pexels.com/video/1/"),
        asset("music_g01_c0", "suno_music", music, type="music"),
    ], "metadata": {}}
    edit = {"version": "1.0", "render_runtime": "ffmpeg", "cuts": [
        {"id": "c1", "source": "RIVER1", "in_seconds": 0, "out_seconds": 5},
        {"id": "c2", "source": "px_1", "in_seconds": 0, "out_seconds": 5},
    ], "metadata": {"audio_layers": [{"role": "A1-music", "asset_id": "music_g01_c0"}]}}
    (project / "work" / "paid_music_ledger.json").write_text(json.dumps({
        "requests": [{"request": 1, "status": "completed", "candidates": [
            {"index": 0, "path": str(music), "outcome": "accepted"}]}], "reviews": []}))
    return manifest, edit


def receipt(project, name="RIVER1_licence.pdf", body=b"%PDF-1.4 licence certificate"):
    path = project / "licenses" / name
    path.write_bytes(body)
    return path


class TestLicences:
    def test_used_references_cover_cuts_opening_and_audio(self):
        edit = {"cuts": [{"id": "c1", "source": "A"}],
                "metadata": {"opening": {"bed_asset_id": "B"},
                             "audio_layers": [{"role": "A1", "asset_id": "C"},
                                              {"role": "A2", "beds": [{"asset_id": "D"}]}]}}
        assert [r["ref"] for r in used_asset_references(edit)] == ["A", "B", "C", "D"]

    def test_used_licensed_stock_without_a_receipt_is_a_blocker(self, project):
        manifest, edit = manifest_and_edit(project)
        report = licence_report(project, manifest, edit)
        assert not report["passed"]
        assert any("RIVER1" in b and "no licence evidence" in b for b in report["blockers"])

    def test_a_recorded_receipt_verifies_and_unused_alternates_do_not_block(self, project):
        manifest, edit = manifest_and_edit(project)
        record_licence_evidence(project, manifest, "RIVER1", receipt(project),
                                licence="Envato Elements", item="RIVER1", verified_by="operator")
        report = licence_report(project, manifest, edit)
        assert report["passed"], report["blockers"]
        assert report["unused_alternates"] == ["ALT9"]
        assert set(report["used_assets"]) == {"RIVER1", "px_1", "music_g01_c0"}

    @pytest.mark.parametrize("tamper", ["delete", "change"])
    def test_a_receipt_deleted_or_changed_after_recording_blocks(self, project, tamper):
        manifest, edit = manifest_and_edit(project)
        path = receipt(project)
        record_licence_evidence(project, manifest, "RIVER1", path, licence="Envato Elements",
                                item="RIVER1", verified_by="operator")
        path.unlink() if tamper == "delete" else path.write_bytes(b"something else")
        assert not licence_report(project, manifest, edit)["passed"]

    def test_evidence_must_be_a_real_file_in_licenses(self, project, tmp_path):
        manifest, _ = manifest_and_edit(project)
        outside = tmp_path / "elsewhere.pdf"
        outside.write_bytes(b"x")
        with pytest.raises(ValueError):
            record_licence_evidence(project, manifest, "RIVER1", outside, licence="x", item="x",
                                    verified_by="op")
        with pytest.raises(ValueError):
            record_licence_evidence(project, manifest, "RIVER1", receipt(project, body=b""),
                                    licence="x", item="x", verified_by="op")

    def test_media_the_manifest_does_not_list_is_unknown_provenance(self, project):
        manifest, edit = manifest_and_edit(project)
        edit["cuts"].append({"id": "c3", "source": "MYSTERY", "in_seconds": 0, "out_seconds": 1})
        assert any("MYSTERY" in b for b in licence_report(project, manifest, edit)["blockers"])

    def test_free_stock_needs_its_licence_and_source_url(self, project):
        manifest, edit = manifest_and_edit(project)
        manifest["assets"][2]["original_url"] = ""
        assert any("px_1" in b for b in licence_report(project, manifest, edit)["blockers"])

    def test_generated_music_needs_an_accepted_ledger_record(self, project):
        manifest, edit = manifest_and_edit(project)
        ledger = project / "work" / "paid_music_ledger.json"
        data = json.loads(ledger.read_text())
        data["requests"][0]["candidates"][0]["outcome"] = "rejected"
        ledger.write_text(json.dumps(data))
        assert any("music_g01_c0" in b for b in licence_report(project, manifest, edit)["blockers"])


class TestGate:
    def _ready_inputs(self, project):
        manifest, edit = manifest_and_edit(project)
        record_licence_evidence(project, manifest, "RIVER1", receipt(project),
                                licence="Envato Elements", item="RIVER1", verified_by="operator")
        mix = channel_mix_from_file(BRAND)
        plan = mix.solve({"A1-music": -18, "A2-water": -10, "A3-birds": -40, "A4-forest": -30})
        edit["metadata"]["mix_balance"] = mix.record(
            plan, plan.verify({r: p.target_lufs for r, p in plan.roles.items()}))
        return manifest, edit

    def test_blockers_are_listed_and_the_checkpoint_is_failed(self, project):
        manifest, edit = manifest_and_edit(project)   # no receipt, no mix record, no master
        readiness = assess_publish_readiness(
            project, proposal_packet={"metadata": {}}, asset_manifest=manifest,
            edit_decisions=edit, render_report={"version": "1.0", "outputs": []},
            channel_brand=BRAND)
        assert readiness["ready"] is False
        kinds = {b.split(":")[0] for b in readiness["blockers"]}
        assert kinds == {"licence", "mix", "technical"}
        write_publish_gate(project.parent, project.name, readiness)
        checkpoint = read_checkpoint(project.parent, project.name, "publish")
        assert checkpoint["status"] == "failed" and "NOT PUBLISH-READY" in checkpoint["error"]
        assert "publish" not in get_completed_stages(project.parent, project.name, "relaxation")

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_a_master_failing_the_contract_blocks_even_with_licences_and_mix(self, project):
        import subprocess

        manifest, edit = self._ready_inputs(project)
        final = project / "output" / "final.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=25:duration=3", "-pix_fmt", "yuvj420p",
                        str(final)], check=True)
        proposal = {"metadata": {"target_duration_seconds": 60, "delivery_canvas": {
            "width": 640, "height": 360, "fps": 30,
            "audio": "aac 48 kHz stereo, -16 LUFS, true peak <= -1.5 dBTP"}}}
        readiness = assess_publish_readiness(
            project, proposal_packet=proposal, asset_manifest=manifest, edit_decisions=edit,
            render_report={"version": "1.0", "outputs": []}, channel_brand=BRAND)
        assert not any(b.startswith(("licence", "mix")) for b in readiness["blockers"])
        assert any(b.startswith("technical") for b in readiness["blockers"])
        assert readiness["final_sha256"]
