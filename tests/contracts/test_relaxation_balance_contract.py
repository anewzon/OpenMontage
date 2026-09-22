"""Contract tests for layer balance and automated music selection.

Two corrections from the first test render are locked here.

BALANCE. The first cut sounded irritating because layers were mixed near
their provider defaults and final loudness normalisation was expected to sort
it out. Normalisation moves the whole mix; it cannot change the relationship
between layers inside it, so birds sitting on top of the music are still on
top of the music at -16 LUFS. The fix is measured per-stem gains against
per-role targets, verified on a preview before the full encode.

SELECTION. Music selection is automated on metadata plus measurement. These
tests hold the honesty line that survives that simplification: the record may
state what was checked, and may not imply a detector ran when none did.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RELAX = ROOT / "skills" / "pipelines" / "relaxation"
MANIFEST = ROOT / "pipeline_defs" / "relaxation.yaml"
BRAND = ROOT.parent / "Channels" / "channel_0001" / "BRAND.md"

FOUNDATION_ROLES = ("music", "water")
SUBORDINATE_ROLES = ("forest", "bird", "wind")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def edit_director() -> str:
    return (RELAX / "edit-director.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def procurement_director() -> str:
    return (RELAX / "procurement-director.md").read_text(encoding="utf-8")


def _brand_or_skip() -> str:
    if not BRAND.is_file():
        pytest.skip("channel_0001/BRAND.md not present (repo checked out standalone)")
    return BRAND.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Balance is measured, not defaulted
# --------------------------------------------------------------------------


def test_gains_must_be_derived_from_measurement(edit_director: str) -> None:
    low = edit_director.lower()
    assert "target" in low and "measured" in low, (
        "edit-director must state that per-stem gain derives from measurement"
    )
    assert "default" in low, "edit-director must reject provider-default gains"


def test_normalisation_is_not_the_balance_fix(
    edit_director: str, manifest: dict
) -> None:
    """The specific misconception that produced the first bad mix."""
    assert re.search(
        r"normalisation.{0,80}cannot", edit_director, re.I | re.S
    ), "edit-director must state that normalisation cannot fix inter-layer balance"

    compose = next(s for s in manifest["stages"] if s["name"] == "compose")
    focus = " ".join(compose["review_focus"]).lower()
    assert "normalisation not used to repair" in focus


def test_preview_is_verified_before_the_full_render(
    edit_director: str, manifest: dict
) -> None:
    low = edit_director.lower()
    assert "preview" in low, "edit-director must require a preview mix"
    assert "before the full render" in low or "before the full encode" in low

    edit = next(s for s in manifest["stages"] if s["name"] == "edit")
    focus = " ".join(edit["review_focus"]).lower()
    assert "preview" in focus, "edit stage must review the balance preview"


def test_unverifiable_balance_must_be_disclosed(edit_director: str) -> None:
    """Honesty survives the simplification: say what could not be judged."""
    low = edit_director.lower()
    assert "could not be verified" in low or "cannot actually be judged" in low
    assert "operator approval" in low


def test_balance_is_persisted_for_reuse(edit_director: str) -> None:
    """A balance that is not written down is not a channel standard."""
    assert "mix_balance" in edit_director, (
        "approved gains must be recorded in edit decisions so later episodes "
        "reproduce the balance rather than rediscovering it"
    )


# --------------------------------------------------------------------------
# The channel owns the taste; the pipeline owns the engineering
# --------------------------------------------------------------------------


def test_channel_declares_per_role_targets() -> None:
    brand = _brand_or_skip()
    assert "Layer balance" in brand, "BRAND.md must declare the balance standard"
    for role in FOUNDATION_ROLES + SUBORDINATE_ROLES:
        assert role in brand.lower(), f"BRAND.md balance table omits role: {role}"


def _parse_prominences(brand: str) -> dict[str, float]:
    """Read the prominence table's real percentages out of BRAND.md.

    Prose alone would let the relationship drift, which is how the second test
    came to mix water as a peer of the music while the channel file still
    described a music-led programme.
    """
    prominences: dict[str, float] = {}
    for line in brand.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = cells[0].lower()
        # "**100% - the reference**" or "**~40% of the music's ...**"
        m = re.search(r"~?\s*(\d+(?:\.\d+)?)\s*%", cells[1])
        if not m:
            continue
        value = float(m.group(1)) / 100.0
        if "combined" in label or ("forest" in label and "wind" in label):
            prominences["ambience_group"] = value
        elif "water" in label:
            prominences["water"] = value
        elif "music" in label:
            prominences["music"] = value
    return prominences


def test_channel_states_a_music_led_relationship() -> None:
    """Music is the reference; water and ambience sit beneath it."""
    brand = _brand_or_skip()
    parsed = _parse_prominences(brand)

    for key in ("music", "water", "ambience_group"):
        assert key in parsed, f"no prominence percentage parsed for: {key}"

    assert parsed["music"] == 1.0, (
        f"music must be the 100% reference layer, parsed {parsed['music']:.0%}"
    )

    # The exact Test 2 defect: water mixed as a peer of the music.
    assert parsed["water"] < parsed["music"], (
        "water must be SUBORDINATE to the music, not its peer - the second "
        "test mixed both at -20 LUFS and the result was not music-led"
    )
    assert parsed["ambience_group"] < parsed["water"], (
        "the combined supporting group must sit below the water"
    )


def test_supporting_ambience_is_constrained_as_one_group() -> None:
    """Forest, birds and wind share one allowance between them.

    Three layers each given the group's full allowance sum roughly 5 dB hotter
    than asked. That arithmetic is what this rule exists to prevent.
    """
    brand = _brand_or_skip()
    low = brand.lower()

    assert "combined" in low, (
        "BRAND.md must state that the supporting layers are constrained as a "
        "COMBINED group"
    )
    assert re.search(r"(each|independent)", low), (
        "BRAND.md must forbid giving each supporting layer its own allowance"
    )

    group_rows = [
        line
        for line in brand.splitlines()
        if line.lstrip().startswith("|") and "combined" in line.lower()
    ]
    assert group_rows, "no combined-group row found in the prominence table"
    row = " ".join(group_rows).lower()
    for member in ("forest", "bird", "wind"):
        assert member in row, f"the combined-group row omits {member}"


def test_percentages_are_not_treated_as_gains_or_lufs_targets() -> None:
    """The figures are a creative relationship, not settings."""
    brand = _brand_or_skip()
    low = brand.lower()
    assert "creative relationship" in low, (
        "BRAND.md must say the percentages express a creative relationship"
    )
    assert "input gains" in low, (
        "BRAND.md must forbid applying the percentages as input gains"
    )
    assert "final lufs targets" in low, (
        "BRAND.md must forbid reading the percentages as LUFS targets"
    )
    assert "reference layer" in low and "does not mean loud" in low, (
        'BRAND.md must state that music at "100%" means reference, not loud'
    )


def test_channel_states_engineering_bands_for_the_relationship() -> None:
    """A creative relationship needs a reproducible dB interpretation."""
    brand = _brand_or_skip()

    bands: dict[str, tuple[float, float]] = {}
    for line in brand.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = re.search(r"(\d+)\s*[–-]\s*(\d+)\s*dB", cells[1])
        if not m:
            continue
        label = cells[0].lower()
        band = (float(m.group(1)), float(m.group(2)))
        if "group" in label or "combined" in label:
            bands["ambience_group"] = band
        elif "water" in label:
            bands["water"] = band

    assert "water" in bands, "BRAND.md states no dB band for the water"
    assert "ambience_group" in bands, (
        "BRAND.md states no dB band for the combined supporting group"
    )
    assert min(bands["water"]) > 0, (
        "the water band must be stated as dB BELOW the music reference"
    )
    assert min(bands["ambience_group"]) >= max(bands["water"]), (
        f"the supporting group's band {bands['ambience_group']} does not sit "
        f"clearly below the water's band {bands['water']}"
    )


def test_pipeline_does_not_hardcode_the_channel_targets(edit_director: str) -> None:
    """Another relaxation channel may want a different balance entirely."""
    assert not re.search(r"−\s*\d+\s*LUFS\s*\|", edit_director), (
        "edit-director carries a per-role target table; that is channel taste "
        "and belongs in BRAND.md"
    )


# --------------------------------------------------------------------------
# Automated selection, honestly recorded
# --------------------------------------------------------------------------


def test_selection_is_automated_without_a_vocal_detector_gate(
    procurement_director: str,
) -> None:
    low = procurement_director.lower()
    # The doc may NAME the old gate in order to say it is gone; what it may
    # not do is impose it.
    assert re.search(
        r"no\s+`?vocals_unverified`?\s+hard stop", low
    ), "procurement must state that the vocal-detector hard stop is removed"
    assert not re.search(
        r"mark .{0,40}`?vocals_unverified`?.{0,40}(escalate|operator decision)", low
    ), "procurement still imposes the vocal-detector escalation gate"
    assert "automated" in low, "procurement must state that selection is automated"


def test_selection_must_not_overclaim(procurement_director: str) -> None:
    """Approved does not mean provable. Both halves are load-bearing."""
    low = procurement_director.lower()
    assert "does **not** prove" in low or "does not prove" in low, (
        "procurement must state that metadata does not prove a track is "
        "vocal-free"
    )
    assert "detector ran" in low, (
        "procurement must forbid implying a detector ran when none did"
    )


def test_selection_compares_a_pool_not_the_first_result(
    procurement_director: str,
) -> None:
    assert "result[0]" in procurement_director, (
        "procurement must reject taking the first search result by default"
    )


def test_unsuitable_music_is_replaceable_rather_than_blocking(
    procurement_director: str,
) -> None:
    low = procurement_director.lower()
    assert "replace it" in low, (
        "an unsuitable track is corrected late, not gated early"
    )


def test_channel_lists_its_own_search_queries() -> None:
    brand = _brand_or_skip()
    low = brand.lower()
    for term in ("piano", "ambient", "without vocals"):
        assert term in low, f"BRAND.md must carry the search preference: {term}"


# --------------------------------------------------------------------------
# Channel-level paid-audio policy (lives in BRAND.md, never in the pipeline)
# --------------------------------------------------------------------------


def _brand_flat_or_skip() -> str:
    return re.sub(r"\s+", " ", _brand_or_skip()).lower()


def _section(flat: str, heading: str) -> str:
    start = flat.index(heading)
    end = flat.find(" ### ", start + len(heading))
    return flat[start:] if end == -1 else flat[start:end]


def test_channel_records_a_budget_aware_long_form_music_policy() -> None:
    """Short films get full unique music; long films do not scale linearly.

    Deliberately not a fixed ratio: the numbers are a planning target, and
    another channel is free to choose a different policy or no music.
    """
    brand = _brand_flat_or_skip()
    policy = _section(brand, "### music programme budget")
    assert re.search(r"1\s*[–-]\s*10 minutes", policy)
    assert "essentially the whole film" in policy
    assert re.search(r"2 hours", policy) and re.search(r"50\s*[–-]\s*60 minutes", policy)
    assert "not linearly" in policy
    assert "5-hour film does **not** request five hours" in policy
    assert "planning target, not an artistic quota" in policy
    assert "no short obvious loops" in policy
    assert "never duplicate the complete mastered first hour" in policy
    assert "before any money is spent" in policy


def test_channel_prefers_generated_instrumental_music_and_screens_every_candidate() -> None:
    brand = _brand_flat_or_skip()
    source = _section(brand, "### music source")
    assert "suno_music" in source and "instrumental: true" in source
    assert "both are screened" in source
    assert "negative_tags" in source


def test_channel_states_its_principal_water_sfx_policy() -> None:
    brand = _brand_flat_or_skip()
    water = _section(brand, "### principal water sfx")
    assert "primary environmental sfx layer" in water
    native = water.index("native water audio from the actual envato footage")
    generated = water.index("elevenlabs_sfx")
    assert native < generated, "native water comes first; generated water is the fallback"
    assert "do not use one identical flowing-water loop across the whole video" in water
    assert "small pool of appropriate loopable water beds" in water
    assert "not a fixed list" in water
    assert "never by generating water audio minute-for-minute" in water
    assert "reuse a water bed only where the visible environment stays compatible" in water
    assert "never layer an equivalent generated water bed on top of already-good native" in water
    assert "music is the reference layer" in water
    assert "combined supporting sfx group stays quieter" in water


def test_channel_music_is_the_reference_not_a_peer_of_the_water() -> None:
    brand = _brand_flat_or_skip()
    assert "it is a peer of the water" not in brand, (
        "BRAND.md contradicted its own music-led balance table"
    )


def test_the_channel_water_rule_does_not_leak_into_the_pipeline() -> None:
    texts = [p.read_text(encoding="utf-8") for p in RELAX.glob("*.md")]
    texts.append(MANIFEST.read_text(encoding="utf-8"))
    for path in ("lib/relaxation_policy.py", "tools/audio/elevenlabs_sfx.py",
                 "tools/audio/suno_music.py"):
        texts.append((ROOT / path).read_text(encoding="utf-8"))
    for text in texts:
        low = re.sub(r"\s+", " ", text).lower()
        assert "principal water sfx" not in low
        assert "flowing-water loop" not in low
        assert "loopable water beds" not in low
