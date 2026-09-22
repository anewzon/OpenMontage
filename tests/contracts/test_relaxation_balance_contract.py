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


def test_foundation_sits_above_the_detail_layers() -> None:
    """Parse the real numbers - prose alone would let the table drift."""
    brand = _brand_or_skip()
    targets: dict[str, float] = {}
    for line in brand.splitlines():
        # Only the balance table: "| **A1 — music** | −20 LUFS | ... |".
        # Match the role in the FIRST cell, because a later cell may mention
        # another role in prose ("a peer of the music").
        m = re.match(
            r"\|\s*\*\*A\d\s*[—-]\s*([^*|]+?)\s*\*\*\s*\|\s*[−-](\d+(?:\.\d+)?)\s*LUFS",
            line,
        )
        if not m:
            continue
        label = m.group(1).lower()
        for role in FOUNDATION_ROLES + SUBORDINATE_ROLES:
            if role in label:
                targets[role] = -float(m.group(2))
                break

    for role in FOUNDATION_ROLES + SUBORDINATE_ROLES:
        assert role in targets, f"no numeric target parsed for role: {role}"

    foundation = min(targets[r] for r in FOUNDATION_ROLES)
    for role in SUBORDINATE_ROLES:
        assert targets[role] <= foundation - 8, (
            f"{role} target {targets[role]} LUFS is not at least 8 LU below the "
            f"foundation ({foundation} LUFS) - this is the defect that made the "
            "first test irritating"
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
