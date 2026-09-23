"""The channel contract: five files, one parser, nothing defaulted.

`lib.channel_policy` reads a channel's BRAND.md profile and ``channel-policy``
block, THUMBNAIL.md and METADATA.md; `lib.stem_balance` reads the
``channel-mix`` block with a generic role model (reference, optional
principal environment, supporting group, optional detail group). These tests
pin the contract: required keys, rejected drift (a thumbnail file that turns
into coordinates, a metadata file that turns into copy), the legacy ``water``
spelling, a channel with no principal layer, and the packaging boundary check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lib.channel_policy import (
    CHANNEL_DIRS,
    CHANNEL_FILES,
    ChannelPolicyError,
    channel_id_of_project,
    channel_policy_from_brand,
    channel_profile_from_brand,
    check_packaging,
    competitor_handles,
    competitor_urls,
    load_channel,
    metadata_policy_from_text,
    prior_uploads,
    publish_inputs,
    thumbnail_policy_from_text,
    title_skeleton,
)
from lib.stem_balance import ChannelMixError, channel_mix_from_brand, channel_mix_from_file

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "relaxation" / "channels"
RIVER = FIXTURES / "river_flow"
V7A_LEGACY = FIXTURES / "reference_v7a_BRAND.md"


def brand_with(policy: str, frontmatter: str = "") -> str:
    fm = frontmatter or ("channel_id: channel_0042\nchannel_name: Fixture\npipeline: relaxation\n"
                         "language: en\nstatus: active\n")
    return f"---\n{fm}---\n\n# channel\n\n```channel-policy\n{policy.strip()}\n```\n"


GOOD_POLICY = """
subjects:
  primary: [rivers]
  allowed: [forest]
  avoid: [roads]
composition:
  camera_movement: required
  static_composition: occasional
audio:
  principal_environment: required
  environment_examples: [water]
  supporting_ambience: optional
  detail_layers: optional
narration: none
opening:
  required: true
  composition: ScenicOpening
  bed: moving water
  text_roles: [brand_signature, welcome_message]
"""


# --------------------------------------------------------------------------
# Profile (frontmatter)
# --------------------------------------------------------------------------


class TestProfile:
    def test_a_valid_profile_parses(self):
        p = channel_profile_from_brand(brand_with(GOOD_POLICY))
        assert (p.channel_id, p.pipeline, p.language, p.status) == \
            ("channel_0042", "relaxation", "en", "active")

    @pytest.mark.parametrize("fm,why", [
        ("channel_name: X\npipeline: relaxation\nlanguage: en\nstatus: active\n", "missing required"),
        ("channel_id: river\nchannel_name: X\npipeline: relaxation\nlanguage: en\nstatus: active\n",
         "four digits"),
        ("channel_id: channel_0001\nchannel_name: X\npipeline: relaxation\nlanguage: English\n"
         "status: active\n", "BCP-47"),
        ("channel_id: channel_0001\nchannel_name: X\npipeline: relaxation\nlanguage: en\n"
         "status: live\n", "one of"),
        ("channel_id: channel_0001\nchannel_name: X\npipeline: relaxation\nlanguage: en\n"
         "status: active\nowner: me\n", "unknown key"),
    ])
    def test_invalid_profiles_are_rejected(self, fm, why):
        with pytest.raises(ChannelPolicyError, match=re.escape(why)):
            channel_profile_from_brand(brand_with(GOOD_POLICY, fm))

    def test_no_frontmatter_is_an_error(self):
        with pytest.raises(ChannelPolicyError, match="frontmatter"):
            channel_profile_from_brand("# just a heading\n")


# --------------------------------------------------------------------------
# channel-policy block
# --------------------------------------------------------------------------


class TestPolicyBlock:
    def test_a_valid_block_parses(self):
        p = channel_policy_from_brand(brand_with(GOOD_POLICY))
        assert p.subjects.verdict("coastal road") == "avoid"
        assert p.subjects.verdict("river") == "primary"
        assert p.subjects.verdict("skyline") == "unlisted"
        assert p.requires_camera_movement and p.allows_static_composition
        assert p.opening.composition == "ScenicOpening" and p.opening.text_roles == \
            ("brand_signature", "welcome_message")
        assert len(p.block_sha256) == 64

    @pytest.mark.parametrize("edit,why", [
        (lambda s: s.replace("avoid: [roads]", "avoid: [roads, rivers]"), "more than one class"),
        (lambda s: s.replace("primary: [rivers]", "primary: []"), "at least one"),
        (lambda s: s.replace("camera_movement: required", "camera_movement: always"), "one of"),
        (lambda s: s.replace("environment_examples: [water]", "environment_examples: []"),
         "must name what"),
        (lambda s: s.replace("principal_environment: required\n  environment_examples: [water]",
                             "principal_environment: none\n  environment_examples: [water]"),
         "must be empty"),
        (lambda s: s.replace("  composition: ScenicOpening\n", ""), "must name the composition"),
        (lambda s: s.replace("required: true", "required: false"), "must be absent"),
        (lambda s: s + "\nextra: 1", "unknown key"),
        (lambda s: s.replace("narration: none\n", ""), "missing required"),
    ])
    def test_invalid_blocks_are_rejected(self, edit, why):
        with pytest.raises(ChannelPolicyError, match=re.escape(why)):
            channel_policy_from_brand(brand_with(edit(GOOD_POLICY)))

    def test_exactly_one_block(self):
        text = brand_with(GOOD_POLICY)
        with pytest.raises(ChannelPolicyError, match="exactly one"):
            channel_policy_from_brand(text + text.split("# channel")[1])
        with pytest.raises(ChannelPolicyError, match="exactly one"):
            channel_policy_from_brand("---\nchannel_id: channel_0001\n---\n")

    def test_no_opening_is_an_explicit_false(self):
        p = channel_policy_from_brand(brand_with(GOOD_POLICY.replace(
            "  required: true\n  composition: ScenicOpening\n  bed: moving water\n"
            "  text_roles: [brand_signature, welcome_message]", "  required: false")))
        assert p.opening.required is False and p.opening.composition is None


# --------------------------------------------------------------------------
# THUMBNAIL.md / METADATA.md: sections present, drift rejected
# --------------------------------------------------------------------------


class TestPolicyDocuments:
    def test_the_fixture_documents_parse(self):
        thumb = thumbnail_policy_from_text((RIVER / "THUMBNAIL.md").read_text(encoding="utf-8"))
        meta = metadata_policy_from_text((RIVER / "METADATA.md").read_text(encoding="utf-8"))
        assert set(thumb.sections) == {"visual_grammar", "typography", "text", "colour",
                                       "subject", "composition", "layout_families", "avoid"}
        assert set(meta.sections) == {"title_style", "description_voice", "keywords", "tags",
                                      "hashtags", "cta", "location_naming", "factual_claims"}

    def test_a_missing_section_is_an_error(self):
        text = (RIVER / "THUMBNAIL.md").read_text(encoding="utf-8").replace("## Typography",
                                                                             "## Fonts")
        with pytest.raises(ChannelPolicyError, match="typography"):
            thumbnail_policy_from_text(text)

    @pytest.mark.parametrize("drift", [
        "Place the title at x: 120, y: 980.",
        "Wordmark 48px in the corner.",
        "Use the template from last week.",
        "Crop to 1280x720 and put the subject at (640, 360).",
        "Always #FF6600 for the type.",
    ])
    def test_a_thumbnail_file_that_becomes_a_template_is_rejected(self, drift):
        text = (RIVER / "THUMBNAIL.md").read_text(encoding="utf-8").replace(
            "## Composition\n", f"## Composition\n\n{drift}\n")
        with pytest.raises(ChannelPolicyError, match="coordinate or template"):
            thumbnail_policy_from_text(text)

    @pytest.mark.parametrize("drift", [
        "Title: {location} Relaxing River Sounds {duration}",
        "Description: [insert place] is a beautiful ...",
        "Use this template for every upload.",
        "<place> ambience for sleep",
    ])
    def test_a_metadata_file_that_becomes_copy_is_rejected(self, drift):
        text = (RIVER / "METADATA.md").read_text(encoding="utf-8").replace(
            "## Title style\n", f"## Title style\n\n{drift}\n")
        with pytest.raises(ChannelPolicyError, match="reusable copy"):
            metadata_policy_from_text(text)

    def test_competitors_are_urls_only(self):
        text = "# heading\n# https://www.youtube.com/@commented\nhttps://www.youtube.com/@real_one\n" \
               "https://www.youtube.com/@real_one\nnot a url\n"
        assert competitor_urls(text) == ("https://www.youtube.com/@real_one",)
        assert competitor_handles(text) == ("real_one",)


# --------------------------------------------------------------------------
# The whole folder
# --------------------------------------------------------------------------


class TestLoadChannel:
    def test_every_fixture_loads_with_all_five_files(self):
        for folder in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
            channel = load_channel(folder)
            assert channel.profile.pipeline == "relaxation"
            for name in CHANNEL_FILES:
                assert (folder / name).is_file()
            for rel in CHANNEL_DIRS:
                assert (folder / rel).is_dir(), f"{folder.name} lacks {rel}"

    def test_a_missing_file_is_an_error(self, tmp_path):
        folder = tmp_path / "channel_0042"
        folder.mkdir()
        for name in CHANNEL_FILES:
            if name != "METADATA.md":
                (folder / name).write_bytes((RIVER / name).read_bytes())
        with pytest.raises(ChannelPolicyError, match=re.escape("['METADATA.md']")):
            load_channel(folder)

    def test_the_folder_and_the_project_must_agree_with_the_profile(self, tmp_path):
        folder = tmp_path / "channel_0042"
        folder.mkdir()
        for name in CHANNEL_FILES:
            (folder / name).write_bytes((RIVER / name).read_bytes())
        with pytest.raises(ChannelPolicyError, match="belongs to"):
            load_channel(folder, expected_id="channel_0042")
        with pytest.raises(ChannelPolicyError, match="permanent id"):
            load_channel(folder)
        assert channel_id_of_project("channel_0042__video_0007") == "channel_0042"
        with pytest.raises(ChannelPolicyError):
            channel_id_of_project("video_0007")

    def test_approved_references_outrank_competitor_references(self, tmp_path):
        folder = tmp_path / "channel_0001"
        for name in CHANNEL_FILES:
            folder.mkdir(exist_ok=True)
            (folder / name).write_bytes((RIVER / name).read_bytes())
        (folder / "thumbnail_refs" / "approved").mkdir(parents=True)
        (folder / "thumbnail_refs" / "competitors").mkdir(parents=True)
        (folder / "thumbnail_refs" / "competitors" / "c.jpg").write_bytes(b"x")
        (folder / "thumbnail_refs" / "approved" / "own.png").write_bytes(b"x")
        (folder / "thumbnail_refs" / "approved" / "notes.txt").write_bytes(b"x")
        refs = load_channel(folder).thumbnail_references()
        assert [r["kind"] for r in refs] == ["approved", "competitor"]
        assert refs[0]["weight"] == "primary" and refs[1]["weight"] == "inspiration"


# --------------------------------------------------------------------------
# channel-mix: generic roles, legacy spelling, no principal, detail group
# --------------------------------------------------------------------------


class TestGenericMixRoles:
    def test_the_legacy_water_spelling_still_reproduces_v7a(self):
        legacy = channel_mix_from_file(V7A_LEGACY)
        modern = channel_mix_from_file(RIVER / "BRAND.md")
        assert legacy.principal_role == modern.principal_role == "A2-water"
        assert legacy.water_role == "A2-water"          # the old accessor still answers
        stems = {"A1-music": -18.2, "A2-water": -9.9, "A3-birds": -41.0, "A4-forest": -30.5}
        for mix in (legacy, modern):
            offsets = {r: p.offset_from_reference_db for r, p in mix.solve(stems).roles.items()}
            assert offsets["A2-water"] == pytest.approx(-29.0, abs=0.15)
            assert offsets["A4-forest"] == pytest.approx(-37.01, abs=0.15)
            assert offsets["A3-birds"] == pytest.approx(-36.43, abs=0.15)
        a = {k: v for k, v in legacy.to_metadata().items() if k not in ("source", "source_sha256",
                                                                        "block_sha256")}
        b = {k: v for k, v in modern.to_metadata().items() if k not in ("source", "source_sha256",
                                                                        "block_sha256")}
        assert a == b, "principal: and water: are the same block"

    def test_both_spellings_at_once_is_an_error(self):
        text = (RIVER / "BRAND.md").read_text(encoding="utf-8").replace(
            "principal:\n", "water:\n  role: X\n  offset_db: -20\n  band_db: [-22, -18]\nprincipal:\n")
        with pytest.raises(ChannelMixError, match="both principal and water"):
            channel_mix_from_brand(text)

    def test_a_channel_with_no_principal_layer_solves_and_records(self):
        mix = channel_mix_from_file(FIXTURES / "city_scenic" / "BRAND.md")
        assert mix.principal_role is None and mix.reference_role == "A2-city"
        plan = mix.solve({"A2-city": -20.0, "A1-music": -15.0})
        assert plan.roles["A1-music"].offset_from_reference_db == pytest.approx(-12.0)
        v = plan.verify({r: p.target_lufs for r, p in plan.roles.items()})
        assert mix.check(v) == []
        record = mix.record(plan, v)
        assert record["channel_mix"]["principal"] is None
        json.dumps(record)

    def test_a_detail_group_is_a_third_tier(self):
        mix = channel_mix_from_file(FIXTURES / "wildlife" / "BRAND.md")
        assert [g.name for g in mix.groups] == ["habitat_support", "creature_detail"]
        plan = mix.solve({"A1-music": -16, "A2-habitat": -12, "A3-wind": -30, "A4-insects": -35,
                          "A5-calls": -28})
        assert plan.roles["A5-calls"].offset_from_reference_db == pytest.approx(-30.0)
        assert plan.group_targets_lufs["creature_detail"] < plan.group_targets_lufs["habitat_support"]

    def test_a_detail_group_alone_or_a_group_above_the_principal_is_rejected(self):
        base = (FIXTURES / "wildlife" / "BRAND.md").read_text(encoding="utf-8")
        start, end = base.index("supporting_group:"), base.index("approved:")
        with pytest.raises(ChannelMixError, match="needs a supporting_group"):
            channel_mix_from_brand(base[:start] + base[end:])
        loud = base.replace("offset_db: -26.0\n  band_db: [-29.0, -23.0]",
                            "offset_db: -10.0\n  band_db: [-12.0, -8.0]")
        with pytest.raises(ChannelMixError, match="louder than the principal"):
            channel_mix_from_brand(loud)

    def test_the_pipeline_holds_no_channel_policy(self):
        """No avoid-list, subject requirement or channel identity in generic text."""
        texts = {p.name: p.read_text(encoding="utf-8").lower()
                 for p in (ROOT / "skills" / "pipelines" / "relaxation").glob("*.md")}
        texts["relaxation.yaml"] = (ROOT / "pipeline_defs" / "relaxation.yaml").read_text(
            encoding="utf-8").lower()
        texts["channel_policy.py"] = (ROOT / "lib" / "channel_policy.py").read_text(
            encoding="utf-8").lower()
        texts["channel_overlay.py"] = (ROOT / "lib" / "channel_overlay.py").read_text(
            encoding="utf-8").lower()
        banned = ("roads, buildings rejected", "roads, traffic and built structures",
                  "strong water subject", "relaxing river sounds", "water must be visibly moving",
                  "moving water from this episode's own footage\"", "river flow naturescapes",
                  "channel_0001", "composition=\"riverflowopening\"", "water_role=",
                  "mix is music-led", "water coherence", "subscribe_button", "channel_0002",
                  "unseen america")
        for name, text in texts.items():
            for phrase in banned:
                assert phrase not in text, f"{name} still binds channel policy: {phrase!r}"


# --------------------------------------------------------------------------
# Publishing: prior uploads and the packaging boundary
# --------------------------------------------------------------------------


def _publish_checkpoint(projects: Path, project_id: str, title: str, description: str = "d"):
    project = projects / project_id
    project.mkdir(parents=True, exist_ok=True)
    (project / "checkpoint_publish.json").write_text(json.dumps({
        "status": "completed",
        "artifacts": {"publish_log": {"version": "1.0", "entries": [
            {"platform": "youtube", "status": "exported", "timestamp": "2026-09-23T00:00:00Z",
             "metadata_used": {"title": title, "description": description,
                               "hashtags": ["#a"]}}]}}}), encoding="utf-8")


class TestPackaging:
    def test_prior_uploads_are_this_channels_only(self, tmp_path):
        _publish_checkpoint(tmp_path, "channel_0001__video_0001", "Misty River Dawn | 2 Hours")
        _publish_checkpoint(tmp_path, "channel_0001__video_0002", "Alpine Stream Rest | 3 Hours")
        _publish_checkpoint(tmp_path, "channel_0002__video_0001", "Route 66 at Blue Hour")
        mine = prior_uploads(tmp_path, "channel_0001", exclude_project="channel_0001__video_0002")
        assert [u["title"] for u in mine] == ["Misty River Dawn | 2 Hours"]

    def test_title_skeleton_masks_the_nouns(self):
        assert title_skeleton("Misty River Dawn | 2 Hours") == title_skeleton(
            "Alpine Stream Rest | 3 Hours")
        assert title_skeleton("A quiet morning on the Snake") != title_skeleton(
            "Misty River Dawn | 2 Hours")

    def test_check_packaging_flags_the_boundaries(self, tmp_path):
        channel = load_channel(RIVER)
        prior = [{"title": "Misty River Dawn | 2 Hours"}]
        other = [{"title": "Big Bend at Blue Hour | 1 Hour"}]
        ok = check_packaging(title="A quiet morning on the Snake", description="Two hours of ...",
                             tags=["river sounds", "sleep"], hashtags=["#river"],
                             channel=channel, prior=prior, other_channels=other)
        assert ok == []
        bad = check_packaging(title="Foggy Creek Morning | 4 Hours", description="",
                              tags=["river sounds", "highway roads"], hashtags=["nohash"],
                              channel=channel, prior=prior, other_channels=other)
        joined = " ".join(bad)
        assert "same formula" in joined and "description is empty" in joined
        assert "subject this channel avoids" in joined and "does not start with #" in joined
        cross = check_packaging(title="Big Sur at Golden Hour | 2 Hours", description="x",
                                tags=["t"], hashtags=["#t"], channel=channel, prior=[],
                                other_channels=other)
        assert any("cross-channel template" in b for b in cross)
        leak = check_packaging(title="Better than CrystalRiver-y3e", description="x", tags=["t"],
                               hashtags=["#t"], channel=channel)
        assert any("competitor" in b for b in leak)

    def test_publish_inputs_bundle_everything_the_director_reads(self, tmp_path):
        _publish_checkpoint(tmp_path, "channel_0001__video_0001", "Misty River Dawn | 2 Hours")
        channel = load_channel(RIVER)
        inputs = publish_inputs(channel=channel, projects_dir=tmp_path,
                                project_id="channel_0001__video_0002")
        assert set(inputs) >= {"channel", "thumbnail_policy", "metadata_policy",
                               "thumbnail_references", "prior_uploads", "language"}
        assert inputs["prior_uploads"][0]["title"] == "Misty River Dawn | 2 Hours"
        assert inputs["language"] == "en"
        json.dumps(inputs)


# --------------------------------------------------------------------------
# The live channels, where present
# --------------------------------------------------------------------------


@pytest.mark.parametrize("channel_id,name", [("channel_0001", "River Flow Naturescapes"),
                                             ("channel_0002", "Unseen America")])
def test_the_live_channel_folders_meet_the_contract(channel_id, name):
    from tests._paths import channel_dir

    folder = channel_dir(channel_id)
    if not (folder / "BRAND.md").is_file():
        pytest.skip(f"{channel_id} not present (repo checked out standalone)")
    channel = load_channel(folder, expected_id=channel_id)
    assert channel.profile.channel_name == name and channel.profile.pipeline == "relaxation"
    for rel in CHANNEL_DIRS:
        assert (folder / rel).is_dir(), f"{channel_id} lacks {rel}"
    channel_mix_from_file(channel.brand_path)


def test_the_live_river_channel_is_water_led_and_reproduces_v7a():
    """The golden reference: channel_0001's live files, unchanged in behaviour."""
    from tests._paths import channel_dir

    folder = channel_dir("channel_0001")
    if not (folder / "BRAND.md").is_file():
        pytest.skip("channel_0001 not present")
    channel = load_channel(folder, expected_id="channel_0001")
    policy = channel.policy
    assert policy.subjects.verdict("flowing water") == "primary"
    assert policy.subjects.is_avoided("roads") and policy.subjects.is_avoided("buildings")
    assert policy.requires_camera_movement and policy.principal_environment == "required"
    assert policy.opening.required and policy.opening.text_roles == (
        "brand_signature", "welcome_message", "episode_line")
    assert "moving water" in policy.opening.bed
    mix = channel_mix_from_file(channel.brand_path)
    plan = mix.solve({"A1-music": -18.2, "A2-water": -9.9, "A3-birds": -41.0, "A4-forest": -30.5})
    offsets = {r: p.offset_from_reference_db for r, p in plan.roles.items()}
    assert offsets["A2-water"] == pytest.approx(-29.0, abs=0.05)
    assert offsets["A4-forest"] == pytest.approx(-37.01, abs=0.05)
    assert offsets["A3-birds"] == pytest.approx(-36.43, abs=0.05)
    assert "no text" in channel.thumbnail["text"].lower()
    assert "water" in channel.metadata["title_style"].lower()


def test_the_live_america_channel_is_place_led_and_not_a_river_clone():
    from tests._paths import channel_dir

    folder = channel_dir("channel_0002")
    if not (folder / "BRAND.md").is_file():
        pytest.skip("channel_0002 not present")
    channel = load_channel(folder, expected_id="channel_0002")
    policy = channel.policy
    for subject in ("roads", "streets", "landmarks", "towns", "mountains", "deserts"):
        assert policy.subjects.verdict(subject) == "primary", subject
    assert policy.subjects.verdict("water") == "optional"
    assert policy.principal_environment == "optional" and policy.narration == "none"
    assert channel.competitors == ("https://www.youtube.com/@scenic_america",)
    river = load_channel(channel_dir("channel_0001"), expected_id="channel_0001")
    assert channel.policy.block_sha256 != river.policy.block_sha256
    assert channel.thumbnail.source_sha256 != river.thumbnail.source_sha256
    assert channel.metadata.source_sha256 != river.metadata.source_sha256
    assert "river" not in channel.brand_text.lower().split("## video format")[0].replace(
        "rivers]", "")   # the policy block may list rivers as allowed; the identity is not a river's
