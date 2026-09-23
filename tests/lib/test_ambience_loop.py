"""Loop beds from generated ambience: trimmed edges, equal-power folds, checked seams."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib.ambience_loop import LoopBedError, build_loop_bed, edge_levels, seam_report

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _generated_source(path: Path, seconds: float = 10.0) -> Path:
    """Pink noise whose first and last 0.3 s sit 4 dB low - as generated loops do."""
    edge = 0.3
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"anoisesrc=color=pink:duration={seconds}:seed=3:amplitude=0.3",
         "-af", f"volume='if(lt(t,{edge})+gt(t,{seconds - edge}),0.631,1)':eval=frame",
         "-ar", "48000", str(path)], check=True)
    return path


def _naive_bed(source: Path, out: Path, times: int) -> Path:
    """What butting passes together produces."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-stream_loop", str(times - 1), "-i",
                    str(source), "-c", "copy", str(out)], check=True)
    return out


def test_the_fixture_really_has_quiet_edges(tmp_path):
    edges = edge_levels(_generated_source(tmp_path / "src.wav"))
    assert edges["head_vs_body_db"] < -3 and edges["tail_vs_body_db"] < -3


def test_butted_passes_fail_the_seam_check(tmp_path):
    src = _generated_source(tmp_path / "src.wav")
    naive = _naive_bed(src, tmp_path / "naive.wav", 4)
    report = seam_report(naive, [10.0, 20.0, 30.0])
    assert report["passed"] is False and report["worst_seam_deviation_db"] > 2.0


def test_a_built_bed_has_clean_seams_and_exact_length(tmp_path):
    src = _generated_source(tmp_path / "src.wav")
    bed = build_loop_bed(src, tmp_path / "bed.wav", 60.0)
    assert bed["crossfade_curve"].startswith("equal-power")
    length = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "default=nw=1:nk=1", bed["path"]], capture_output=True, text=True).stdout)
    assert length == pytest.approx(60.0, abs=0.05)
    assert len(bed["seam_seconds"]) >= 6
    report = seam_report(bed["path"], bed["seam_seconds"])
    assert report["passed"] is True, report
    assert report["worst_seam_deviation_db"] < 1.0


def test_a_linear_fold_would_dip_where_equal_power_does_not(tmp_path):
    """Why the curve matters: a linear crossfade of uncorrelated noise loses ~3 dB."""
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    for path, seed in ((a, 1), (b, 2)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"anoisesrc=color=pink:duration=8:seed={seed}:amplitude=0.3",
                        str(path)], check=True)
    results = {}
    for curve in ("tri", "qsin"):
        out = tmp_path / f"{curve}.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a), "-i", str(b),
                        "-filter_complex", f"acrossfade=d=4:c1={curve}:c2={curve}",
                        str(out)], check=True)
        results[curve] = seam_report(out, [6.0])["seams"][0]["deviation_db"]
    assert results["tri"] < -2.0 and abs(results["qsin"]) < 1.0, results


def test_a_source_too_short_to_fold_is_refused(tmp_path):
    src = _generated_source(tmp_path / "short.wav", seconds=3.0)
    with pytest.raises(LoopBedError):
        build_loop_bed(src, tmp_path / "bed.wav", 30.0)
