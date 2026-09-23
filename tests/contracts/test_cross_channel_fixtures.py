"""Six channels, one pipeline, no code change, no policy leakage.

The `relaxation` pipeline is a scenic/relaxation production grammar. These
fixtures prove that River Flow, Unseen America, Wildlife, City Scenic, Forest
and Fireplace are all served by the same code through their own five policy
files - and that no channel's policy reaches another: a river channel's
"avoid roads" never leaks into the city channel, a fireplace's "static is
fine" never leaks into the river, and each channel's thumbnail grammar and
publishing language are its own. River Flow is the locked golden reference.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from lib.channel_policy import Channel, check_packaging, load_channel
from lib.stem_balance import channel_mix_from_file

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "relaxation" / "channels"
NAMES = ("river_flow", "unseen_america", "wildlife", "city_scenic", "forest", "fireplace")


@pytest.fixture(scope="module")
def channels() -> dict[str, Channel]:
    return {name: load_channel(FIXTURES / name) for name in NAMES}


@pytest.fixture(scope="module")
def mixes():
    return {name: channel_mix_from_file(FIXTURES / name / "BRAND.md") for name in NAMES}


# --------------------------------------------------------------------------
# Each configuration, as the task states it
# --------------------------------------------------------------------------


def test_river_flow_is_water_led_roads_avoided_v7a_image_led(channels, mixes):
    c, m = channels["river_flow"], mixes["river_flow"]
    assert c.policy.subjects.verdict("flowing water") == "primary"
    assert c.policy.subjects.is_avoided("roads") and c.policy.subjects.is_avoided("buildings")
    assert c.policy.principal_environment == "required" and c.policy.requires_camera_movement
    assert m.principal_role == "A2-water" and m.principal_offset_db == -29.0
    assert m.group_offset_db == -33.7 and m.members["A3-birds"] == 0.48
    assert "no text" in c.thumbnail["text"].lower()
    assert "sleep" in c.metadata["description_voice"].lower()


def test_unseen_america_allows_places_roads_and_needs_no_water(channels, mixes):
    c = channels["unseen_america"]
    for subject in ("american cities", "streets", "historic districts", "mountains", "deserts",
                    "coastlines", "towns", "roads", "landmarks"):
        assert c.policy.subjects.verdict(subject) == "primary", subject
    assert c.policy.subjects.verdict("water") == "optional"
    assert c.policy.principal_environment == "optional"
    assert c.policy.narration == "none"
    assert "place" in c.thumbnail["visual_grammar"].lower()
    assert "location" in c.metadata["title_style"].lower() or \
        "place" in c.metadata["title_style"].lower()
    assert c.competitors == ("https://www.youtube.com/@scenic_america",)


def test_wildlife_is_animal_led_with_optional_water_and_wildlife_sound(channels, mixes):
    c, m = channels["wildlife"], mixes["wildlife"]
    assert c.policy.subjects.verdict("animals") == "primary"
    assert c.policy.subjects.verdict("water") in ("allowed", "optional")
    assert "wildlife sound" in c.policy.environment_examples
    assert c.policy.opening.required is False
    assert m.principal_role == "A2-habitat" and m.detail_group is not None


def test_city_scenic_permits_architecture_roads_traffic_people_and_needs_no_nature(channels,
                                                                                   mixes):
    c, m = channels["city_scenic"]. policy, mixes["city_scenic"]
    for subject in ("architecture", "roads", "traffic", "people in passing"):
        assert c.subjects.verdict(subject) in ("primary", "allowed"), subject
    assert c.subjects.verdict("water") == "optional"
    assert "city ambience" in c.environment_examples
    assert m.principal_role is None and m.reference_role == "A2-city"


def test_forest_is_forest_first_with_optional_water_and_forest_ambience(channels, mixes):
    c, m = channels["forest"], mixes["forest"]
    assert c.policy.subjects.verdict("forest") == "primary"
    assert c.policy.subjects.verdict("water") == "optional"
    assert "forest ambience" in c.policy.environment_examples
    assert m.principal_role == "A2-forest" and m.principal_offset_db == -22.0


def test_fireplace_allows_static_composition_and_needs_no_camera_movement(channels, mixes):
    c, m = channels["fireplace"], mixes["fireplace"]
    assert c.policy.camera_movement == "none" and c.policy.static_composition == "allowed"
    assert c.policy.environment_examples == ("fire crackle",)
    assert c.policy.opening.required is False
    assert m.reference_role == "A2-crackle" and m.principal_role is None


# --------------------------------------------------------------------------
# No leakage
# --------------------------------------------------------------------------


def test_river_policy_does_not_leak_into_any_other_channel(channels):
    river = channels["river_flow"].policy
    for name in NAMES[1:]:
        other = channels[name].policy
        # "avoid roads" is River Flow's; the city and America channels allow them.
        if name in ("unseen_america", "city_scenic"):
            assert not other.subjects.is_avoided("roads"), name
        # "water required" is River Flow's alone.
        assert other.subjects.verdict("flowing water") != "primary", name
        assert other.block_sha256 != river.block_sha256


def test_no_two_channels_share_a_policy_mix_thumbnail_or_metadata(channels, mixes):
    for a, b in itertools.combinations(NAMES, 2):
        assert channels[a].policy.block_sha256 != channels[b].policy.block_sha256, (a, b)
        assert mixes[a].block_sha256 != mixes[b].block_sha256, (a, b)
        assert channels[a].thumbnail.source_sha256 != channels[b].thumbnail.source_sha256, (a, b)
        assert channels[a].metadata.source_sha256 != channels[b].metadata.source_sha256, (a, b)


def test_the_same_subject_gets_a_different_verdict_per_channel(channels):
    verdicts = {name: channels[name].policy.subjects.verdict("roads") for name in NAMES}
    assert verdicts["river_flow"] == "avoid" and verdicts["forest"] == "avoid"
    assert verdicts["unseen_america"] == "primary" and verdicts["city_scenic"] == "allowed"
    assert verdicts["fireplace"] == "unlisted"


def test_the_same_stems_get_a_different_mix_per_channel(mixes):
    """One set of measured stems, six channel blocks, six different relationships."""
    stems = {"A1-music": -18.0, "A2-water": -10.0, "A2-ambience": -10.0, "A2-habitat": -10.0,
             "A2-forest": -10.0, "A2-city": -10.0, "A2-crackle": -10.0, "A3-birds": -40.0,
             "A4-forest": -30.0, "A4-wind": -30.0, "A3-distant-traffic": -30.0,
             "A4-insects": -35.0, "A5-calls": -28.0, "A3-room": -30.0}
    results = {}
    for name, mix in mixes.items():
        present = {r: v for r, v in stems.items() if r in mix.all_roles}
        plan = mix.solve(present)
        results[name] = tuple(sorted((r, round(p.offset_from_reference_db, 2))
                                     for r, p in plan.roles.items()))
    assert len(set(results.values())) == len(NAMES)
    river = dict(results["river_flow"])
    assert river["A2-water"] == pytest.approx(-29.0) and river["A3-birds"] == pytest.approx(-36.43, abs=0.01)
    assert river["A4-forest"] == pytest.approx(-37.01, abs=0.01)


def test_thumbnail_grammar_is_isolated(channels):
    """River wants no text and a water subject; America wants a place name; nothing bleeds."""
    river, america, fire = (channels[n].thumbnail for n in ("river_flow", "unseen_america",
                                                            "fireplace"))
    assert "water" in river["subject"].lower()
    assert "water" not in fire["subject"].lower()
    assert "place" in america["text"].lower() and "prefer no text" not in america["text"].lower()
    assert "prefer no text" in river["text"].lower()
    for name in NAMES:
        for body in channels[name].thumbnail.sections.values():
            assert "px" not in body.lower() and "coordinate" not in body.lower()


def test_metadata_language_is_isolated(channels):
    river, america, wild = (channels[n].metadata for n in ("river_flow", "unseen_america",
                                                           "wildlife"))
    assert "water" in river["title_style"].lower()
    assert "water" not in america["title_style"].lower()
    assert "species" in wild["title_style"].lower()
    for name in NAMES:
        for body in channels[name].metadata.sections.values():
            assert "{" not in body and "template" not in body.lower()


def test_a_river_title_formula_is_a_cross_channel_template_for_america(channels):
    """Mechanical cross-channel title templates are caught by check_packaging."""
    america = channels["unseen_america"]
    river_uploads = [{"title": "Misty River Dawn | 2 Hours"}]
    blockers = check_packaging(title="Sleepy Mountain Town | 2 Hours", description="x",
                               tags=["small town", "scenic drive"], hashtags=["#idaho"],
                               channel=america, other_channels=river_uploads)
    assert any("cross-channel template" in b for b in blockers)
    fresh = check_packaging(title="An evening walk through Bisbee, Arizona", description="x",
                            tags=["small town", "scenic drive"], hashtags=["#arizona"],
                            channel=america, other_channels=river_uploads)
    assert fresh == []


def test_tags_are_screened_by_each_channels_own_avoid_list(channels):
    river, city = channels["river_flow"], channels["city_scenic"]
    tags = ["traffic sounds", "city lights"]
    assert any("avoids" in b for b in check_packaging(
        title="A", description="b", tags=tags, hashtags=["#x"], channel=river))
    assert check_packaging(title="A", description="b", tags=tags, hashtags=["#x"],
                           channel=city) == []
