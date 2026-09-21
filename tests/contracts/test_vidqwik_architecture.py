"""Contract tests protecting the VidQwik architecture lock.

These guard structural facts, not wording: a skill exists, it is registered,
a Director references the reusable skill rather than duplicating it, and no
channel identity has leaked into a generic meta skill. They should survive
rewording and fail on architectural drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
META = SKILLS / "meta"
INDEX = SKILLS / "INDEX.md"

VIDQWIK_META_SKILLS = ["asset-procurement", "vidqwik-pipeline-authoring"]

# Identity that belongs in a channel's BRAND.md, never in a generic meta skill.
# A reusable skill that names these cannot serve a second channel or niche.
CHANNEL_IDENTITY_TOKENS = [
    "River Flow Naturescapes",
    "channel_0001",
    "CrystalRiver",
    "Crystal River",
]


@pytest.mark.parametrize("name", VIDQWIK_META_SKILLS)
def test_vidqwik_meta_skill_exists(name: str) -> None:
    path = META / f"{name}.md"
    assert path.is_file(), f"Missing reusable meta skill: {path}"
    assert path.read_text(encoding="utf-8").strip(), f"{path} is empty"


@pytest.mark.parametrize("name", VIDQWIK_META_SKILLS)
def test_vidqwik_meta_skill_registered_in_index(name: str) -> None:
    index = INDEX.read_text(encoding="utf-8")
    assert f"meta/{name}.md" in index, (
        f"meta/{name}.md is not registered in skills/INDEX.md. "
        f"An unregistered skill is invisible to a fresh agent."
    )


@pytest.mark.parametrize("name", VIDQWIK_META_SKILLS)
def test_reusable_skill_carries_no_channel_identity(name: str) -> None:
    """A generic meta skill must serve any channel and any niche."""
    body = (META / f"{name}.md").read_text(encoding="utf-8")
    leaked = [t for t in CHANNEL_IDENTITY_TOKENS if t in body]
    assert not leaked, (
        f"meta/{name}.md names channel-specific identity {leaked}. "
        f"That belongs in Channels/<channel_id>/BRAND.md — a reusable skill "
        f"naming one channel cannot be reused by another."
    )


def test_relaxation_procurement_director_references_reusable_skill() -> None:
    """The pipeline Director must reference the meta skill, not re-implement it."""
    body = (SKILLS / "pipelines" / "relaxation" / "procurement-director.md").read_text(
        encoding="utf-8"
    )
    assert "meta/asset-procurement.md" in body, (
        "relaxation procurement-director must reference "
        "skills/meta/asset-procurement.md rather than duplicating procurement logic."
    )


def test_relaxation_procurement_director_carries_no_channel_identity() -> None:
    """The pipeline must serve any relaxation channel, not just the first one."""
    body = (SKILLS / "pipelines" / "relaxation" / "procurement-director.md").read_text(
        encoding="utf-8"
    )
    leaked = [t for t in CHANNEL_IDENTITY_TOKENS if t in body]
    assert not leaked, (
        f"relaxation procurement-director names {leaked}. Subject, season and "
        f"brand come from the channel's BRAND.md and the approved proposal at runtime."
    )


def test_procurement_remains_a_native_human_gate() -> None:
    """Procurement must stop for a human; it must never auto-download."""
    manifest = yaml.safe_load(
        (ROOT / "pipeline_defs" / "relaxation.yaml").read_text(encoding="utf-8")
    )
    stage = next(
        (s for s in manifest["stages"] if s.get("name") == "procurement"), None
    )
    assert stage is not None, "relaxation pipeline lost its procurement stage"
    assert stage.get("human_approval_default") is True, (
        "procurement must require human approval — it is the licensing/download gate"
    )
    assert stage.get("checkpoint_required") is True

    body = (SKILLS / "pipelines" / "relaxation" / "procurement-director.md").read_text(
        encoding="utf-8"
    )
    assert "awaiting_human" in body, (
        "procurement-director must write an awaiting_human checkpoint and stop"
    )


def test_every_relaxation_stage_skill_resolves() -> None:
    """Every skill the manifest names must exist on disk."""
    manifest = yaml.safe_load(
        (ROOT / "pipeline_defs" / "relaxation.yaml").read_text(encoding="utf-8")
    )
    refs = set(manifest.get("required_skills", []))
    refs |= {s["skill"] for s in manifest["stages"] if s.get("skill")}
    refs.add(manifest["orchestration"]["skill"])
    missing = [r for r in sorted(refs) if not (SKILLS / f"{r}.md").is_file()]
    assert not missing, f"relaxation manifest references missing skills: {missing}"


def test_asset_procurement_requires_exact_items_not_search_pages() -> None:
    """The employee must never be handed a search page to choose from.

    Structural check: the skill has to distinguish item URLs from search URLs.
    """
    body = (META / "asset-procurement.md").read_text(encoding="utf-8").lower()
    assert "exact-item" in body or "exact item" in body
    assert "search" in body, "skill must address search pages to rule them out"


def test_asset_procurement_is_two_pass() -> None:
    body = (META / "asset-procurement.md").read_text(encoding="utf-8").lower()
    assert "pass 1" in body and "pass 2" in body, (
        "asset-procurement must teach the two-pass screening process"
    )
