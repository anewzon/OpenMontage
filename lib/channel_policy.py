"""The channel contract: five human-editable files, read at runtime, never restated.

A VidQwik channel is configuration, not software. Everything that makes one
channel differ from another lives in ``Channels/<channel_id>/``:

    BRAND.md          identity, look, subjects, audio roles, opening, mix
    COMPETITORS.md    one YouTube channel URL per line (market reference only)
    RESEARCH.md       that channel's research rules
    THUMBNAIL.md      thumbnail grammar - language and boundaries, never a template
    METADATA.md       publishing/SEO language - style and boundaries, never copy

plus the optional persistent folders ``brand_assets/``,
``thumbnail_refs/competitors/`` and ``thumbnail_refs/approved/``.

This module is the ONE parser for that contract. It follows the pattern
`lib.stem_balance.channel_mix_from_brand` set for the ``channel-mix`` block:
exact keys, nothing defaulted, an unknown key is an error. Three things are
machine-read:

1. **The channel profile** - the YAML frontmatter of ``BRAND.md``
   (``channel_id``, ``channel_name``, ``pipeline``, ``language``, ``status``).
2. **The ``channel-policy`` block** - one fenced YAML block in ``BRAND.md``
   stating the channel's subject policy (primary / allowed / avoid), its
   composition grammar (whether camera movement is required, whether static
   composition is allowed), its audio grammar (whether a principal
   environment layer exists, and what it may be), narration, and the opening
   (required or not, which composition, what bed).
3. **THUMBNAIL.md and METADATA.md** - Markdown sections. They are read as
   *policy text* for the Publish Director; what this module checks is that
   every required section exists and that neither file has drifted into a
   template: no pixel coordinates or fixed layout boxes in THUMBNAIL.md, no
   fill-in-the-blank copy in METADATA.md.

The relaxation pipeline holds no channel policy of its own. A river channel
requires water and avoids roads; a city channel allows roads and needs no
water; a fireplace channel allows static composition and needs no camera
movement. Each says so here, and the same pipeline code serves all of them.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

# --------------------------------------------------------------------------
# Contract constants
# --------------------------------------------------------------------------

#: The five human-editable channel files. All five must exist.
CHANNEL_FILES = ("BRAND.md", "COMPETITORS.md", "RESEARCH.md", "THUMBNAIL.md", "METADATA.md")
#: Optional persistent folders.
CHANNEL_DIRS = ("brand_assets", "thumbnail_refs/competitors", "thumbnail_refs/approved")
#: What THUMBNAIL.md must state (as ``## Heading`` sections; matched loosely).
THUMBNAIL_SECTIONS = {
    "visual_grammar": ("visual grammar",),
    "typography": ("typography",),
    "text": ("text",),
    "colour": ("colour", "color"),
    "subject": ("subject",),
    "composition": ("composition",),
    "layout_families": ("layout",),
    "avoid": ("avoid",),
}
#: What METADATA.md must state.
METADATA_SECTIONS = {
    "title_style": ("title",),
    "description_voice": ("description",),
    "keywords": ("keyword",),
    "tags": ("tags",),
    "hashtags": ("hashtag",),
    "cta": ("call to action", "cta"),
    "location_naming": ("location",),
    "factual_claims": ("factual", "claims"),
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
STATUS_VALUES = ("planned", "active", "paused", "retired")
_ID = re.compile(r"^channel_\d{4}$")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?$", re.S | re.M)
_POLICY_BLOCK = re.compile(r"^```channel-policy[ \t]*\r?\n(.*?)^```[ \t]*\r?$", re.S | re.M)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*\r?$", re.M)
_YOUTUBE = re.compile(r"https?://(?:www\.)?youtube\.com/(@[\w.\-]+|channel/[\w\-]+|c/[\w.\-]+)",
                      re.I)

#: THUMBNAIL.md is grammar, not a template. Any of these turns it into one.
_COORDINATE_PATTERNS = (
    re.compile(r"\b\d{2,4}\s*(?:px|pt)\b", re.I),                  # 120px, 48 pt
    re.compile(r"\b[xy]\s*[:=]\s*-?\d+", re.I),                     # x: 40, y=1200
    re.compile(r"\(\s*\d+\s*,\s*\d+\s*\)"),                          # (120, 640)
    re.compile(r"\b\d{3,4}\s*[x×]\s*\d{3,4}\b"),                     # 1280x720 boxes
    re.compile(r"\b(?:top|bottom|left|right)\s*[:=]\s*\d+", re.I),  # left: 120
    re.compile(r"(?<!\w)#[0-9a-f]{6}\b", re.I),                    # fixed hex swatches
    re.compile(r"\bcoordinates?\b", re.I),
    re.compile(r"\btemplate\b", re.I),
)
#: METADATA.md is language and boundaries, not copy. Any of these is copy.
_COPY_PATTERNS = (
    re.compile(r"\{[^}\n]{1,40}\}"),                     # {location} fill-ins
    re.compile(r"\[(?:insert|your|the)\b[^\]\n]*\]", re.I),   # [insert place]
    re.compile(r"<[a-z_ ]{2,30}>", re.I),                     # <place>
    re.compile(r"\btemplate\b", re.I),
    re.compile(r"\bboilerplate\b", re.I),
)

POLICY_TOP_KEYS = {"subjects", "composition", "audio", "narration", "opening", "overlays"}
POLICY_REQUIRED = {"subjects", "composition", "audio", "narration", "opening"}
SUBJECT_KEYS = {"primary", "allowed", "optional", "avoid"}
COMPOSITION_KEYS = {"camera_movement", "static_composition"}
AUDIO_KEYS = {"principal_environment", "environment_examples", "supporting_ambience",
              "detail_layers"}
OPENING_KEYS = {"required", "composition", "bed", "text_roles"}
LEVELS = ("required", "preferred", "optional", "none")
ALLOWANCE = ("allowed", "occasional", "avoid")
NARRATION = ("none", "optional", "required")


class ChannelPolicyError(ValueError):
    """The channel contract is missing, invalid or has become a template."""


# --------------------------------------------------------------------------
# Small validators (the stem_balance pattern)
# --------------------------------------------------------------------------


def _exact_keys(data: Any, allowed: set[str], required: set[str], where: str) -> Mapping:
    if not isinstance(data, Mapping):
        raise ChannelPolicyError(f"{where} must be a mapping")
    unknown = set(data) - allowed
    if unknown:
        raise ChannelPolicyError(f"{where} has unknown key(s) {sorted(unknown)} - a typo "
                                 "would otherwise be ignored silently")
    missing = required - set(data)
    if missing:
        raise ChannelPolicyError(f"{where} is missing required key(s) {sorted(missing)}")
    return data


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChannelPolicyError(f"{where} must be non-empty text")
    return value.strip()


def _choice(value: Any, choices: Iterable[str], where: str) -> str:
    value = _text(value, where).lower()
    if value not in choices:
        raise ChannelPolicyError(f"{where} must be one of {list(choices)}, got {value!r}")
    return value


def _terms(value: Any, where: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None and allow_empty:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ChannelPolicyError(f"{where} must be a list of terms")
    terms = tuple(_text(v, where).lower() for v in value)
    if not terms and not allow_empty:
        raise ChannelPolicyError(f"{where} must name at least one term")
    if len(set(terms)) != len(terms):
        raise ChannelPolicyError(f"{where} lists a term twice")
    return terms


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 1. The channel profile (BRAND.md frontmatter)
# --------------------------------------------------------------------------

PROFILE_KEYS = {"channel_id", "channel_name", "pipeline", "language", "status", "format"}
PROFILE_REQUIRED = {"channel_id", "channel_name", "pipeline", "language", "status"}


@dataclass(frozen=True)
class ChannelProfile:
    """The durable identity of a channel, exactly as its BRAND.md frontmatter states it."""

    channel_id: str
    channel_name: str
    pipeline: str
    language: str
    status: str
    format: Optional[str] = None

    def to_metadata(self) -> dict[str, Any]:
        return {"channel_id": self.channel_id, "channel_name": self.channel_name,
                "pipeline": self.pipeline, "language": self.language, "status": self.status,
                "format": self.format}


def channel_profile_from_brand(brand_text: str) -> ChannelProfile:
    """Parse the YAML frontmatter of a BRAND.md. Nothing is defaulted."""
    import yaml

    match = _FRONTMATTER.match(brand_text.lstrip("﻿"))
    if not match:
        raise ChannelPolicyError("BRAND.md must open with a YAML frontmatter block (---)")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ChannelPolicyError(f"BRAND.md frontmatter is not valid YAML: {exc}") from exc
    data = _exact_keys(data, PROFILE_KEYS, PROFILE_REQUIRED, "BRAND.md frontmatter")
    channel_id = _text(data["channel_id"], "channel_id")
    if not _ID.match(channel_id):
        raise ChannelPolicyError(f"channel_id must be channel_ plus four digits, got {channel_id!r}")
    language = _text(data["language"], "language").lower()
    if not re.match(r"^[a-z]{2,3}(-[a-z]{2,4})?$", language):
        raise ChannelPolicyError(f"language must be a BCP-47 tag such as en or en-us, got "
                                 f"{language!r}")
    status = _choice(data["status"], STATUS_VALUES, "status")
    fmt = data.get("format")
    return ChannelProfile(
        channel_id=channel_id, channel_name=_text(data["channel_name"], "channel_name"),
        pipeline=_text(data["pipeline"], "pipeline").lower(), language=language,
        status=status, format=None if fmt is None else _text(fmt, "format"))


# --------------------------------------------------------------------------
# 2. The channel-policy block (BRAND.md)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectPolicy:
    primary: tuple[str, ...]
    allowed: tuple[str, ...]
    optional: tuple[str, ...]
    avoid: tuple[str, ...]

    def verdict(self, subject: str) -> str:
        """``primary`` / ``allowed`` / ``optional`` / ``avoid`` / ``unlisted`` for a term.

        Matching is whole-word, case-insensitive, either direction (the
        listed term inside the description or the description inside the
        listed term), so ``"roads"`` matches ``"coastal road at dusk"``.
        """
        text = subject.lower().strip()
        for name in ("avoid", "primary", "allowed", "optional"):
            for term in getattr(self, name):
                if _word_match(term, text):
                    return name
        return "unlisted"

    def is_avoided(self, subject: str) -> bool:
        return self.verdict(subject) == "avoid"


def _word_match(term: str, text: str) -> bool:
    stem = re.escape(term.rstrip("s"))
    return re.search(rf"\b{stem}(?:s|es)?\b", text) is not None or \
        re.search(rf"\b{re.escape(text.rstrip('s'))}(?:s|es)?\b", term) is not None


@dataclass(frozen=True)
class OpeningPolicy:
    required: bool
    composition: Optional[str]
    bed: Optional[str]
    text_roles: tuple[str, ...]


@dataclass(frozen=True)
class ChannelPolicy:
    """A channel's production grammar, exactly as its ``channel-policy`` block states it."""

    subjects: SubjectPolicy
    camera_movement: str        # required | preferred | optional | none
    static_composition: str     # allowed | occasional | avoid
    principal_environment: str  # required | optional | none
    environment_examples: tuple[str, ...]
    supporting_ambience: str    # required | optional | none
    detail_layers: str          # required | optional | none
    narration: str              # none | optional | required
    opening: OpeningPolicy
    block_sha256: str
    #: Channel-owned persistent overlays (subscribe animation, logo, corner bug);
    #: empty for a channel that declares none. See lib.channel_overlay.
    overlays: tuple = ()

    @property
    def requires_camera_movement(self) -> bool:
        return self.camera_movement == "required"

    @property
    def allows_static_composition(self) -> bool:
        return self.static_composition != "avoid"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "subjects": {"primary": list(self.subjects.primary),
                         "allowed": list(self.subjects.allowed),
                         "optional": list(self.subjects.optional),
                         "avoid": list(self.subjects.avoid)},
            "composition": {"camera_movement": self.camera_movement,
                            "static_composition": self.static_composition},
            "audio": {"principal_environment": self.principal_environment,
                      "environment_examples": list(self.environment_examples),
                      "supporting_ambience": self.supporting_ambience,
                      "detail_layers": self.detail_layers},
            "narration": self.narration,
            "opening": {"required": self.opening.required,
                        "composition": self.opening.composition, "bed": self.opening.bed,
                        "text_roles": list(self.opening.text_roles)},
            "block_sha256": self.block_sha256,
            "overlays": [o.to_metadata() for o in self.overlays],
        }


def channel_policy_from_brand(brand_text: str) -> ChannelPolicy:
    """Parse and validate the ONE ``channel-policy`` block in a BRAND.md."""
    import yaml

    blocks = _POLICY_BLOCK.findall(brand_text)
    if len(blocks) != 1:
        raise ChannelPolicyError(
            f"BRAND.md must contain exactly one ```channel-policy block; found {len(blocks)}")
    block = blocks[0]
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise ChannelPolicyError(f"the channel-policy block is not valid YAML: {exc}") from exc
    data = _exact_keys(data, POLICY_TOP_KEYS, POLICY_REQUIRED, "channel-policy")
    from lib.channel_overlay import OverlayError, overlay_policies_from_data

    try:
        overlays = overlay_policies_from_data(data.get("overlays"))
    except OverlayError as exc:
        raise ChannelPolicyError(str(exc)) from exc

    subj = _exact_keys(data["subjects"], SUBJECT_KEYS, {"primary", "avoid"},
                       "channel-policy.subjects")
    subjects = SubjectPolicy(
        primary=_terms(subj["primary"], "subjects.primary", allow_empty=False),
        allowed=_terms(subj.get("allowed"), "subjects.allowed"),
        optional=_terms(subj.get("optional"), "subjects.optional"),
        avoid=_terms(subj["avoid"], "subjects.avoid"))
    everything = [*subjects.primary, *subjects.allowed, *subjects.optional, *subjects.avoid]
    clashes = sorted({t for t in everything if everything.count(t) > 1})
    if clashes:
        raise ChannelPolicyError(f"subject term(s) listed in more than one class: {clashes}")

    comp = _exact_keys(data["composition"], COMPOSITION_KEYS, COMPOSITION_KEYS,
                       "channel-policy.composition")
    audio = _exact_keys(data["audio"], AUDIO_KEYS, AUDIO_KEYS, "channel-policy.audio")
    principal = _choice(audio["principal_environment"], ("required", "optional", "none"),
                        "audio.principal_environment")
    examples = _terms(audio["environment_examples"], "audio.environment_examples")
    if principal == "none" and examples:
        raise ChannelPolicyError("audio.environment_examples must be empty when "
                                 "principal_environment is none")
    if principal != "none" and not examples:
        raise ChannelPolicyError("audio.environment_examples must name what the principal "
                                 "environment may be")

    opening_data = _exact_keys(data["opening"], OPENING_KEYS, {"required"},
                               "channel-policy.opening")
    required = opening_data["required"]
    if not isinstance(required, bool):
        raise ChannelPolicyError("opening.required must be true or false")
    composition = opening_data.get("composition")
    bed = opening_data.get("bed")
    roles = _terms(opening_data.get("text_roles"), "opening.text_roles")
    if required and not composition:
        raise ChannelPolicyError("opening.composition must name the composition when the "
                                 "opening is required")
    if not required and (composition or bed or roles):
        raise ChannelPolicyError("opening.composition/bed/text_roles must be absent when "
                                 "the opening is not required")

    return ChannelPolicy(
        subjects=subjects,
        camera_movement=_choice(comp["camera_movement"], LEVELS, "composition.camera_movement"),
        static_composition=_choice(comp["static_composition"], ALLOWANCE,
                                   "composition.static_composition"),
        principal_environment=principal, environment_examples=examples,
        supporting_ambience=_choice(audio["supporting_ambience"], ("required", "optional", "none"),
                                    "audio.supporting_ambience"),
        detail_layers=_choice(audio["detail_layers"], ("required", "optional", "none"),
                              "audio.detail_layers"),
        narration=_choice(data["narration"], NARRATION, "narration"),
        opening=OpeningPolicy(required=required,
                              composition=None if composition is None
                              else _text(composition, "opening.composition"),
                              bed=None if bed is None else _text(bed, "opening.bed"),
                              text_roles=roles),
        block_sha256=_sha(block), overlays=overlays)


# --------------------------------------------------------------------------
# 3. THUMBNAIL.md and METADATA.md - sectioned policy text
# --------------------------------------------------------------------------


def _sections(text: str) -> dict[str, str]:
    """``{heading (lowercase): body}`` for every Markdown heading in the file."""
    out: dict[str, str] = {}
    matches = list(_HEADING.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(2).strip().lower()] = text[m.end():end].strip()
    return out


def _find_section(sections: Mapping[str, str], needles: Iterable[str]) -> Optional[str]:
    for heading, body in sections.items():
        if any(n in heading for n in needles):
            return body
    return None


@dataclass(frozen=True)
class PolicyDocument:
    """A sectioned policy file (THUMBNAIL.md or METADATA.md), validated but not interpreted."""

    name: str
    sections: Mapping[str, str]
    source_sha256: str

    def __getitem__(self, key: str) -> str:
        return self.sections[key]

    def to_metadata(self) -> dict[str, Any]:
        return {"file": self.name, "sections": sorted(self.sections),
                "source_sha256": self.source_sha256}


def _parse_policy_document(text: str, name: str, required: Mapping[str, tuple[str, ...]],
                           forbidden: Iterable[re.Pattern], what: str) -> PolicyDocument:
    sections = _sections(text)
    found: dict[str, str] = {}
    missing = []
    for key, needles in required.items():
        body = _find_section(sections, needles)
        if body is None or not body.strip():
            missing.append(key)
        else:
            found[key] = body
    if missing:
        raise ChannelPolicyError(f"{name} is missing section(s) {missing} (a ## heading "
                                 "containing that word, with text under it)")
    for key, body in found.items():
        for pattern in forbidden:
            hit = pattern.search(body)
            if hit:
                raise ChannelPolicyError(
                    f"{name} section {key!r} contains {hit.group(0)!r}: {what}")
    return PolicyDocument(name=name, sections=found, source_sha256=_sha(text))


def thumbnail_policy_from_text(text: str) -> PolicyDocument:
    """THUMBNAIL.md: grammar, typography, text preference, colour, subject, composition,
    layout families and an avoid-list. Never coordinates or a fixed template."""
    return _parse_policy_document(
        text, "THUMBNAIL.md", THUMBNAIL_SECTIONS, _COORDINATE_PATTERNS,
        "THUMBNAIL.md describes a visual grammar; it must not become a coordinate or "
        "template system")


def metadata_policy_from_text(text: str) -> PolicyDocument:
    """METADATA.md: title style, description voice, keywords, tags, hashtags, CTA,
    location naming and factual-claim rules. Language and boundaries, never copy."""
    return _parse_policy_document(
        text, "METADATA.md", METADATA_SECTIONS, _COPY_PATTERNS,
        "METADATA.md defines language and boundaries; it must not hold reusable copy or "
        "fill-in placeholders")


def competitor_urls(text: str) -> tuple[str, ...]:
    """YouTube channel URLs in COMPETITORS.md (``#`` lines are comments)."""
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _YOUTUBE.search(line)
        if m:
            urls.append(m.group(0))
    return tuple(dict.fromkeys(urls))


def competitor_handles(text: str) -> tuple[str, ...]:
    """The ``@handle`` / channel names that must never appear in published text."""
    handles = []
    for url in competitor_urls(text):
        tail = url.rsplit("/", 1)[-1]
        handles.append(tail.lstrip("@").lower())
    return tuple(handles)


# --------------------------------------------------------------------------
# 4. The whole channel, from disk
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    """Everything the pipeline may read about a channel, resolved from its folder."""

    root: Path
    profile: ChannelProfile
    policy: ChannelPolicy
    thumbnail: PolicyDocument
    metadata: PolicyDocument
    competitors: tuple[str, ...]
    research_rules: str
    brand_text: str
    approved_thumbnails: tuple[Path, ...] = field(default_factory=tuple)
    competitor_thumbnails: tuple[Path, ...] = field(default_factory=tuple)
    brand_assets: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def channel_id(self) -> str:
        return self.profile.channel_id

    @property
    def brand_path(self) -> Path:
        return self.root / "BRAND.md"

    def thumbnail_references(self) -> list[dict[str, Any]]:
        """Reference images, strongest first: the channel's own approved thumbnails,
        then competitor references (inspiration only, never assets to copy)."""
        refs = [{"path": str(p), "kind": "approved", "weight": "primary",
                 "use": "the channel's own approved thumbnail - the reference to grow toward"}
                for p in self.approved_thumbnails]
        refs += [{"path": str(p), "kind": "competitor", "weight": "inspiration",
                  "use": "market reference only - never copied, cropped or traced"}
                 for p in self.competitor_thumbnails]
        return refs

    def to_metadata(self) -> dict[str, Any]:
        return {"root": str(self.root), "profile": self.profile.to_metadata(),
                "policy": self.policy.to_metadata(), "thumbnail": self.thumbnail.to_metadata(),
                "metadata": self.metadata.to_metadata(), "competitors": list(self.competitors),
                "approved_thumbnails": len(self.approved_thumbnails),
                "competitor_thumbnails": len(self.competitor_thumbnails),
                "brand_sha256": _sha(self.brand_text)}


def _images(folder: Path) -> tuple[Path, ...]:
    if not folder.is_dir():
        return ()
    return tuple(sorted(p for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES))


def load_channel(channel_dir: str | Path, *, expected_id: Optional[str] = None) -> Channel:
    """Load and validate a channel folder. Every one of the five files is required.

    ``expected_id`` (normally the id in the project's name) must match the
    profile's ``channel_id`` - a project never runs under another channel's
    policy by pointing at the wrong folder.
    """
    root = Path(channel_dir)
    missing = [name for name in CHANNEL_FILES if not (root / name).is_file()]
    if missing:
        raise ChannelPolicyError(f"{root} is missing channel file(s) {missing}; every channel "
                                 f"needs all of {list(CHANNEL_FILES)}")
    texts = {name: (root / name).read_text(encoding="utf-8") for name in CHANNEL_FILES}
    brand = texts["BRAND.md"]
    profile = channel_profile_from_brand(brand)
    if expected_id and profile.channel_id != expected_id:
        raise ChannelPolicyError(f"{root / 'BRAND.md'} says channel_id {profile.channel_id!r}, "
                                 f"but the project belongs to {expected_id!r}")
    if _ID.match(root.name) and root.name != profile.channel_id:
        # A production channel folder IS the permanent id. (A test fixture may
        # sit in a folder named after its scenario; expected_id covers that.)
        raise ChannelPolicyError(f"folder {root.name!r} does not match channel_id "
                                 f"{profile.channel_id!r}; the folder is the permanent id")
    return Channel(
        root=root, profile=profile, policy=channel_policy_from_brand(brand),
        thumbnail=thumbnail_policy_from_text(texts["THUMBNAIL.md"]),
        metadata=metadata_policy_from_text(texts["METADATA.md"]),
        competitors=competitor_urls(texts["COMPETITORS.md"]),
        research_rules=texts["RESEARCH.md"], brand_text=brand,
        approved_thumbnails=_images(root / "thumbnail_refs" / "approved"),
        competitor_thumbnails=_images(root / "thumbnail_refs" / "competitors"),
        brand_assets=tuple(sorted(p for p in (root / "brand_assets").glob("*")
                                  if p.is_file())) if (root / "brand_assets").is_dir() else (),
    )


def find_channels_root(start: str | Path) -> Path:
    """The ``Channels`` folder beside the OpenMontage checkout, searching upward."""
    start = Path(start).resolve()
    for base in (start, *start.parents):
        candidate = base / "Channels"
        if candidate.is_dir() and (candidate / "CHANNELS.md").is_file():
            return candidate
    raise ChannelPolicyError(f"no Channels/ folder (with CHANNELS.md) above {start}")


def channel_id_of_project(project_id: str) -> str:
    """``channel_0042`` from ``channel_0042__video_0007``."""
    head = project_id.split("__", 1)[0]
    if not _ID.match(head):
        raise ChannelPolicyError(f"project id {project_id!r} does not start with a channel id")
    return head


def load_channel_for_project(project_id: str, *, channels_root: str | Path) -> Channel:
    channel_id = channel_id_of_project(project_id)
    return load_channel(Path(channels_root) / channel_id, expected_id=channel_id)


# --------------------------------------------------------------------------
# 5. Publish inputs: prior uploads and the packaging boundary check
# --------------------------------------------------------------------------


def prior_uploads(projects_dir: str | Path, channel_id: str,
                  *, exclude_project: Optional[str] = None) -> list[dict[str, Any]]:
    """Titles and descriptions this channel already published (from publish checkpoints).

    Read from every ``projects/<channel_id>__*/checkpoint_publish.json`` whose
    publish_log carries ``metadata_used``. Other channels' uploads are never
    returned: they are the wrong reference for this channel's language.
    """
    projects_dir = Path(projects_dir)
    if not projects_dir.is_dir():
        return []
    found = []
    for project in sorted(projects_dir.glob(f"{channel_id}__*")):
        if exclude_project and project.name == exclude_project:
            continue
        checkpoint = project / "checkpoint_publish.json"
        if not checkpoint.is_file():
            continue
        try:
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        log = ((data.get("artifacts") or {}).get("publish_log") or {})
        for entry in log.get("entries") or []:
            used = entry.get("metadata_used") or {}
            if used.get("title"):
                found.append({"project_id": project.name, "title": used["title"],
                              "description": used.get("description", ""),
                              "hashtags": list(used.get("hashtags") or [])})
    return found


def title_skeleton(title: str) -> str:
    """The shape of a title with its content words masked.

    Numbers become ``#``, capitalised words become ``_``; lower-case function
    words stay. Two titles with the same skeleton are the same formula with
    the nouns swapped - the mechanical template the channel contract forbids.
    """
    words = re.findall(r"[A-Za-z0-9'’]+|[|:•–—-]", title)
    out = []
    for w in words:
        if re.fullmatch(r"\d+[A-Za-z]*", w):
            out.append("#")
        elif w[0].isupper():
            out.append("_")
        else:
            out.append(w.lower())
    return " ".join(out)


def check_packaging(*, title: str, description: str, tags: Iterable[str],
                    hashtags: Iterable[str], channel: Channel,
                    prior: Iterable[Mapping[str, Any]] = (),
                    other_channels: Iterable[Mapping[str, Any]] = ()) -> list[str]:
    """Blockers for a packaging that breaks the channel's boundaries.

    Checks structure, not taste: empty fields, competitor names in viewer
    text, subjects the channel avoids used as tags, a title that repeats a
    prior upload or shares its formula, and a formula shared with another
    channel's upload (a cross-channel template).
    """
    blockers: list[str] = []
    tags = [t.strip() for t in tags if t and t.strip()]
    hashtags = [h.strip() for h in hashtags if h and h.strip()]
    if not title.strip():
        blockers.append("title is empty")
    if not description.strip():
        blockers.append("description is empty")
    if not tags:
        blockers.append("no tags")
    viewer_text = f"{title}\n{description}\n{' '.join(tags)}\n{' '.join(hashtags)}".lower()
    for handle in competitor_handles("\n".join(channel.competitors)):
        if handle and handle in viewer_text:
            blockers.append(f"competitor {handle!r} named in viewer-facing text")
    for tag in tags:
        if channel.policy.subjects.is_avoided(tag):
            blockers.append(f"tag {tag!r} is a subject this channel avoids")
    for h in hashtags:
        if not h.startswith("#"):
            blockers.append(f"hashtag {h!r} does not start with #")
    skeleton = title_skeleton(title)
    norm = re.sub(r"\s+", " ", title.strip().lower())
    for upload in prior:
        old = upload.get("title", "")
        if re.sub(r"\s+", " ", old.strip().lower()) == norm:
            blockers.append(f"title repeats the channel's earlier upload {old!r}")
        elif title_skeleton(old) == skeleton and len(skeleton.split()) >= 3:
            blockers.append(f"title is the same formula as the earlier upload {old!r} with "
                            "the nouns swapped")
    for upload in other_channels:
        old = upload.get("title", "")
        if title_skeleton(old) == skeleton and len(skeleton.split()) >= 3:
            blockers.append(f"title shares its formula with another channel's upload "
                            f"{old!r} - a cross-channel template")
    return blockers


def publish_inputs(*, channel: Channel, projects_dir: str | Path, project_id: str,
                   research_brief: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Everything the Publish Director reads before writing packaging, in one record.

    Recorded in ``publish_log.metadata.channel_inputs`` so a later review can
    see which policy hashes and references the packaging was made from.
    """
    prior = prior_uploads(projects_dir, channel.channel_id, exclude_project=project_id)
    return {
        "channel": channel.to_metadata(),
        "thumbnail_policy": dict(channel.thumbnail.sections),
        "metadata_policy": dict(channel.metadata.sections),
        "thumbnail_references": channel.thumbnail_references(),
        "prior_uploads": prior,
        "research_brief_present": research_brief is not None,
        "language": channel.profile.language,
    }
