"""The channel owns its mix: one validated ``channel-mix`` block in BRAND.md.

The relaxation pipeline holds no mix numbers. A production parses its
channel's block (`channel_mix_from_brand`), solves the balance from it with
the channel's bands enforced, and records the parsed values and their hash in
``edit_decisions.metadata.mix_balance``; the publish gate re-derives them
(`check_mix_record`). These tests use a checked-in snapshot of the approved
v7a block and a deliberately different second channel, plus real synthesised
stems measured with FFmpeg.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.stem_balance import (
    ChannelMixError,
    StemBalanceError,
    channel_mix_from_brand,
    channel_mix_from_file,
    check_mix_record,
    measure_stem,
    solve_balance,
)
from tests._paths import channel_brand

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "relaxation" / "channels"
V7A = FIXTURES / "reference_v7a_BRAND.md"
SECOND = FIXTURES / "second_channel_BRAND.md"

#: What the operator approved by ear (audio_report_v7.json, preview v7a):
#: measured offsets from the music, without a wind stem.
V7A_MEASURED = {"A2-water": -28.9, "A3-birds": -36.4, "A4-forest": -37.0}


def block(text: str) -> str:
    return f"# channel\n\n```channel-mix\n{text.strip()}\n```\n"


GOOD = """
reference_role: A1-music
master_target_lufs: -16.0
water: {role: A2-water, offset_db: -29.0, band_db: [-31.0, -27.0]}
supporting_group:
  name: g
  offset_db: -33.7
  band_db: [-36.0, -32.0]
  members: {A3-birds: 0.5, A4-forest: 0.5}
"""


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------


class TestParsing:
    def test_a_valid_block_parses_with_its_hash_and_source(self):
        mix = channel_mix_from_file(V7A)
        assert mix.water_role == "A2-water" and mix.water_band_db == (-31.0, -27.0)
        assert mix.group_offset_db == -33.7 and mix.members["A3-birds"] == 0.48
        assert mix.water_treatment_af.startswith("highshelf=f=4000")
        assert len(mix.block_sha256) == 64 and mix.source == str(V7A)

    @pytest.mark.parametrize("text,why", [
        ("# no block at all\n", "exactly one"),
        (block(GOOD) + block(GOOD), "exactly one"),
        (block(GOOD + "\nsurprise: 1\n"), "unknown key"),
        (block(GOOD.replace("reference_role: A1-music\n", "")), "missing required"),
        (block(GOOD.replace("band_db: [-31.0, -27.0]", "band_db: [-27.0, -31.0]")), "low < high"),
        (block(GOOD.replace("offset_db: -29.0", "offset_db: -20.0")), "outside its own band"),
        (block(GOOD.replace("A3-birds: 0.5", "A3-birds: 0")), "positive"),
        (block(GOOD.replace("A3-birds: 0.5", "A2-water: 0.5")), "more than once"),
        (block(GOOD.replace("offset_db: -33.7", "offset_db: -25.0")
               .replace("band_db: [-36.0, -32.0]", "band_db: [-26.0, -24.0]")), "louder than"),
        (block(GOOD.replace("master_target_lufs: -16.0", "master_target_lufs: loud")), "number"),
        (block(GOOD.replace("band_db: [-31.0, -27.0]", "band_db: [-31.0]")), "[low, high]"),
        (block(GOOD.replace("band_db: [-31.0, -27.0]", "band_db: [-80.0, -27.0]")), "outside"),
        (block("water: [unclosed"), "YAML"),
    ])
    def test_invalid_blocks_are_rejected(self, text, why):
        with pytest.raises(ChannelMixError, match=re.escape(why)):
            channel_mix_from_brand(text)

    def test_there_is_no_silent_default_band(self):
        mix = channel_mix_from_brand(block(GOOD))
        with pytest.raises(StemBalanceError, match="no default"):
            solve_balance(mix.spec(), {"A1-music": -16, "A2-water": -10, "A3-birds": -30,
                                       "A4-forest": -30}, water_role="A2-water")
        import lib.stem_balance as sb

        assert not hasattr(sb, "WATER_BAND_DB") and not hasattr(sb, "SUPPORT_BAND_DB")

    def test_the_pipeline_holds_no_channel_mix_numbers(self):
        texts = [(ROOT / "lib" / "stem_balance.py").read_text(encoding="utf-8")]
        texts += [p.read_text(encoding="utf-8")
                  for p in (ROOT / "skills" / "pipelines" / "relaxation").glob("*.md")]
        texts.append((ROOT / "pipeline_defs" / "relaxation.yaml").read_text(encoding="utf-8"))
        for text in texts:
            for number in ("-29.0", "33.7", "27–31", "32–36", "0.42", "0.48", "-8.0, -6.0"):
                assert number not in text, number


# --------------------------------------------------------------------------
# The approved reference reproduces automatically
# --------------------------------------------------------------------------


class TestReproduction:
    def test_a_new_production_reproduces_v7a_from_the_block(self):
        mix = channel_mix_from_file(V7A)
        # Arbitrary built-stem levels: the relationship, not the gains, is fixed.
        plan = mix.solve({"A1-music": -18.2, "A2-water": -9.9, "A3-birds": -41.0,
                          "A4-forest": -30.5})
        offsets = {r: p.offset_from_reference_db for r, p in plan.roles.items()}
        for role, approved in V7A_MEASURED.items():
            assert offsets[role] == pytest.approx(approved, abs=0.15), role
        assert any("share the whole group allowance" in n for n in plan.notes)

    def test_a_second_channel_gets_a_different_mix_from_the_same_stems(self):
        stems = {"A1-music": -18.0, "A2-water": -10.0, "A3-birds": -40.0, "A4-forest": -30.0}
        rf = channel_mix_from_file(V7A).solve(stems)
        other = channel_mix_from_file(SECOND)
        mapped = {"M1-music": -18.0, "W1-surf": -10.0, "R1-rain": -40.0, "R2-wind": -30.0}
        plan = other.solve(mapped)
        assert plan.roles["W1-surf"].offset_from_reference_db == pytest.approx(-12.0)
        assert rf.roles["A2-water"].offset_from_reference_db == pytest.approx(-29.0)
        assert plan.gains_db["W1-surf"] != rf.gains_db["A2-water"]
        assert other.water_treatment_af is None

    def test_a_missing_required_stem_is_an_error_not_a_skip(self):
        with pytest.raises(ChannelMixError, match="required role"):
            channel_mix_from_file(V7A).solve({"A1-music": -16.0, "A3-birds": -30.0})


# --------------------------------------------------------------------------
# Recording and the edit/publish check
# --------------------------------------------------------------------------


def _edit(mix, stems, remeasured):
    plan = mix.solve(stems)
    verification = plan.verify(remeasured)
    return {"metadata": {"mix_balance": mix.record(plan, verification)}}, plan


class TestRecord:
    STEMS = {"A1-music": -18.0, "A2-water": -10.0, "A3-birds": -40.0, "A4-forest": -30.0}

    def _executed(self, plan):
        return {r: p.target_lufs for r, p in plan.roles.items()}

    def test_a_faithful_record_passes(self):
        mix = channel_mix_from_file(V7A)
        plan = mix.solve(self.STEMS)
        edit = {"metadata": {"mix_balance": mix.record(plan, plan.verify(self._executed(plan)))}}
        assert check_mix_record(edit, mix) == []
        recorded = edit["metadata"]["mix_balance"]["channel_mix"]
        assert recorded["block_sha256"] == mix.block_sha256 and recorded["source"] == str(V7A)

    def test_a_mix_solved_from_another_block_is_blocked(self):
        rf, other = channel_mix_from_file(V7A), channel_mix_from_brand(block(GOOD))
        plan = other.solve(self.STEMS)
        edit = {"metadata": {"mix_balance": other.record(plan, plan.verify(self._executed(plan)))}}
        assert any("hash mismatch" in b for b in check_mix_record(edit, rf))

    def test_an_unrecorded_or_unverified_mix_is_blocked(self):
        mix = channel_mix_from_file(V7A)
        assert check_mix_record({"metadata": {}}, mix)
        plan = mix.solve(self.STEMS)
        edit = {"metadata": {"mix_balance": mix.record(plan)}}
        assert any("re-measured" in b for b in check_mix_record(edit, mix))

    def test_an_executed_mix_outside_the_band_is_blocked(self):
        mix = channel_mix_from_file(V7A)
        plan = mix.solve(self.STEMS)
        executed = self._executed(plan)
        executed["A2-water"] += 6.0          # water 6 dB too loud - the old default's defect
        edit = {"metadata": {"mix_balance": mix.record(plan, plan.verify(executed))}}
        blockers = check_mix_record(edit, mix)
        assert any("outside the channel's bands" in b for b in blockers)

    def test_hand_edited_offsets_are_blocked(self):
        mix = channel_mix_from_file(V7A)
        plan = mix.solve(self.STEMS)
        edit = {"metadata": {"mix_balance": mix.record(plan, plan.verify(self._executed(plan)))}}
        edit["metadata"]["mix_balance"]["roles"]["A2-water"]["offset_from_reference_db"] = -8.0
        assert any("A2-water" in b for b in check_mix_record(edit, mix))

    def test_a_group_quieter_than_its_band_is_a_failure_too(self):
        mix = channel_mix_from_file(V7A)
        plan = mix.solve(self.STEMS)
        executed = self._executed(plan)
        executed["A3-birds"] -= 8.0
        executed["A4-forest"] -= 8.0
        assert any("group ambience_group" in f for f in mix.check(plan.verify(executed)))


# --------------------------------------------------------------------------
# The live channel file, where present
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_brand() -> str:
    path = channel_brand("channel_0001")
    if not path.is_file():
        pytest.skip("channel_0001/BRAND.md not present (repo checked out standalone)")
    return path.read_text(encoding="utf-8")


def test_the_live_channel_block_parses_and_matches_the_approved_snapshot(live_brand):
    live = channel_mix_from_brand(live_brand)
    snap = channel_mix_from_file(V7A)
    assert live.to_metadata() | {"source": None, "source_sha256": None} == \
        snap.to_metadata() | {"source": None, "source_sha256": None}


def test_the_live_block_agrees_with_the_channel_tables(live_brand):
    """BRAND.md promises this check: its prose tables and its block must agree."""
    live = channel_mix_from_brand(live_brand)
    water = re.search(r"Flowing water \(A2\)[^|]*\|\s*\*\*(\d+)–(\d+) dB\*\* — approved point "
                      r"(\d+(?:\.\d+)?) dB", live_brand)
    group = re.search(r"Combined birds[^|]*\|\s*\*\*(\d+)–(\d+) dB\*\* — approved point "
                      r"(\d+(?:\.\d+)?) dB", live_brand)
    assert water and group, "the engineering table rows were not found"
    assert (-float(water.group(2)), -float(water.group(1))) == live.water_band_db
    assert -float(water.group(3)) == live.water_offset_db
    assert (-float(group.group(2)), -float(group.group(1))) == live.group_band_db
    assert -float(group.group(3)) == live.group_offset_db


# --------------------------------------------------------------------------
# Real stems: solve, apply, re-measure
# --------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_stems_land_on_the_v7a_relationship(tmp_path):
    sources = {
        "A1-music": ("sine=frequency=330:duration=6", -3),
        "A2-water": ("anoisesrc=color=pink:duration=6", -1),
        "A3-birds": ("sine=frequency=3200:duration=6", -25),
        "A4-forest": ("anoisesrc=color=brown:duration=6", -12),
    }
    built = {}
    for role, (src, vol) in sources.items():
        path = tmp_path / f"{role}.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-af", f"volume={vol}dB",
                        "-ar", "48000", str(path)], capture_output=True, check=True)
        built[role] = path
    mix = channel_mix_from_file(V7A)
    plan = mix.solve({r: measure_stem(p).integrated_lufs for r, p in built.items()})
    remeasured = {}
    for role, path in built.items():
        out = tmp_path / f"{role}_gain.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(path), "-af",
                        f"volume={plan.gains_db[role]}dB", str(out)],
                       capture_output=True, check=True)
        remeasured[role] = measure_stem(out).integrated_lufs
    verification = plan.verify(remeasured, tolerance_lu=0.3)
    assert mix.check(verification) == []
    for role, approved in V7A_MEASURED.items():
        assert verification.achieved_offsets_db[role] == pytest.approx(approved, abs=0.3), role
