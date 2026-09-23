"""Contract tests for the relaxation pipeline's sourcing-mode field.

The procurement stage branches on a sourcing mode carried by the approved
`proposal_packet`. The manifest and the Procurement Director both used to
point at `production_plan.sourcing`, a location the schema forbids:
`production_plan` is `additionalProperties: false`, so an artifact written
there would be REJECTED at checkpoint time. The real first run stored the
value under `metadata.sourcing`, which the schema does allow.

That mismatch is the kind that survives review because nothing executes a
doc string. These tests make the documented path and the schema agree, and
lock both modes so a later edit cannot quietly drop one.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "artifacts" / "proposal_packet.schema.json"
MANIFEST_PATH = ROOT / "pipeline_defs" / "relaxation.yaml"
DIRECTOR_PATH = ROOT / "skills" / "pipelines" / "relaxation" / "procurement-director.md"

SOURCING_MODES = ["licensed_manual", "free_auto"]

# The path the docs are allowed to name, and the one they must not.
VALID_REF = "metadata.sourcing"
INVALID_REF = "production_plan.sourcing"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def director_text() -> str:
    return DIRECTOR_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Why the valid location is the valid one
# --------------------------------------------------------------------------


def test_production_plan_is_closed_and_rejects_sourcing(schema: dict) -> None:
    """The root cause. If this ever opens up, the docs may point back."""
    production_plan = schema["properties"]["production_plan"]
    assert production_plan.get("additionalProperties") is False, (
        "production_plan is no longer closed; re-evaluate where sourcing belongs "
        "before changing the documented path"
    )
    assert "sourcing" not in production_plan["properties"], (
        "production_plan now declares 'sourcing' - the documented reference "
        "should move there deliberately, not drift"
    )


def test_metadata_accepts_sourcing(schema: dict) -> None:
    """The documented location must actually be writable."""
    metadata = schema["properties"]["metadata"]
    assert metadata.get("additionalProperties") is not False, (
        "metadata is now closed; metadata.sourcing would be rejected at "
        "checkpoint time and the documented path is no longer valid"
    )


# --------------------------------------------------------------------------
# Both modes must survive real schema validation at the documented location
# --------------------------------------------------------------------------


#: A synthetic, schema-minimal packet checked in with the tests. It used to be
#: read from a production project under the git-ignored ``projects/`` folder,
#: so these tests failed in any clean checkout or worktree.
MINIMAL_PACKET = ROOT / "tests" / "fixtures" / "relaxation" / "proposal_packet_minimal.json"


def _minimal_packet(schema: dict, sourcing: str) -> dict:
    """The smallest packet the schema accepts, carrying `sourcing`."""
    packet = json.loads(MINIMAL_PACKET.read_text(encoding="utf-8"))
    packet.setdefault("metadata", {})["sourcing"] = sourcing
    return packet


def test_the_fixture_is_valid_and_synthetic(schema: dict) -> None:
    """Without `sourcing` it validates, and it carries no production data."""
    packet = json.loads(MINIMAL_PACKET.read_text(encoding="utf-8"))
    jsonschema.validate(instance=packet, schema=schema)
    text = MINIMAL_PACKET.read_text(encoding="utf-8").lower()
    for marker in ("channel_0", "river flow", "envato", "licen"):
        assert marker not in text


def test_no_contract_test_reads_production_projects() -> None:
    """Tests must pass in a clean checkout: projects/ is git-ignored."""
    import re

    # A path built into a production project: "projects" then a channel_NNNN
    # project id, whether joined with "/" in a string or with Path's "/".
    production_path = re.compile(
        r"""projects["']?\s*(?:\)\s*)?[/\\]\s*(?:\(\s*)?\n?\s*["']?channel_\d{4}__""")
    offenders = [path.relative_to(ROOT).as_posix()
                 for path in (ROOT / "tests").rglob("*.py")
                 if production_path.search(path.read_text(encoding="utf-8", errors="replace"))]
    assert offenders == []


@pytest.mark.parametrize("mode", SOURCING_MODES)
def test_sourcing_mode_validates_at_the_documented_location(
    schema: dict, mode: str
) -> None:
    """Both modes, written where the docs say, pass schema validation."""
    packet = _minimal_packet(schema, mode)
    jsonschema.validate(instance=packet, schema=schema)
    assert packet["metadata"]["sourcing"] == mode


@pytest.mark.parametrize("mode", SOURCING_MODES)
def test_sourcing_mode_is_rejected_at_the_old_location(schema: dict, mode: str) -> None:
    """The old documented path genuinely fails - this is not a style preference."""
    packet = _minimal_packet(schema, mode)
    del packet["metadata"]["sourcing"]
    packet["production_plan"]["sourcing"] = mode
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=packet, schema=schema)


# --------------------------------------------------------------------------
# The docs must name the valid location, and only that one
# --------------------------------------------------------------------------


def test_manifest_references_the_valid_sourcing_path(manifest: dict) -> None:
    procurement = next(s for s in manifest["stages"] if s["name"] == "procurement")
    review_focus = " ".join(procurement["review_focus"])
    assert VALID_REF in review_focus, (
        "procurement review_focus no longer names the sourcing location; "
        "a reviewer cannot check a mode that is never read"
    )
    assert INVALID_REF not in review_focus


def test_director_references_the_valid_sourcing_path(director_text: str) -> None:
    assert VALID_REF in director_text
    assert INVALID_REF not in director_text, (
        "Procurement Director points at a schema-forbidden path"
    )


@pytest.mark.parametrize("mode", SOURCING_MODES)
def test_director_still_documents_both_modes(director_text: str, mode: str) -> None:
    """Correcting a field path must not quietly delete a branch."""
    assert f"`{mode}`" in director_text, f"Procurement Director dropped mode: {mode}"


@pytest.mark.parametrize("mode", SOURCING_MODES)
def test_manifest_review_focus_covers_both_modes(manifest: dict, mode: str) -> None:
    procurement = next(s for s in manifest["stages"] if s["name"] == "procurement")
    combined = " ".join(procurement["review_focus"] + procurement["success_criteria"])
    assert mode in combined, f"procurement stage no longer reviews mode: {mode}"


def test_human_gate_survives_in_both_modes(manifest: dict, director_text: str) -> None:
    """The gate is the point of the stage; neither mode may bypass it."""
    procurement = next(s for s in manifest["stages"] if s["name"] == "procurement")
    assert procurement["human_approval_default"] is True
    assert "Human gate preserved in BOTH modes" in " ".join(procurement["review_focus"])
    assert "In either mode the human gate remains" in director_text
