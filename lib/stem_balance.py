"""Translate a creative loudness *relationship* into measured stem gains.

The problem this solves
----------------------
An operator describes a mix as a relationship: "music is the reference, the
flowing water sits well under it, forest and birds and wind together sit
under that." Those figures describe a **creative relationship**, not
settings. Three ways of using them are wrong:

1. **As literal input gains.** Fixed volume factors applied to raw source
   files ignores that recordings arrive at wildly different levels. Measured
   on this installation, a river bed came off the wire at -9.9 LUFS while the
   music sat at -16.0: applying 0.4 to the river leaves it *louder* than the
   music it is supposed to sit beneath.

2. **As final LUFS targets.** A percentage is not a loudness unit.

3. **Per layer instead of per group.** Giving forest, birds and wind each a
   20% allowance produces a supporting group roughly 5 dB louder than the 20%
   that was asked for, because three sources at the same level sum. This is
   the defect that makes a nature mix feel crowded.

What this module does instead
-----------------------------
* Reads the **measured loudness of the assembled stem** — after concatenation,
  crossfades and any corrective processing — not the mean of the source files.
  Test 2 established why: a water stem built from files averaging -16.3 LUFS
  measured -9.9 LUFS once assembled. Gain computed from the source mean would
  have been 6 dB wrong.

* Treats a percentage as an **amplitude ratio**, so `offset_db = 20*log10(p)`.
  The channel's stated bands remain the authority; the formula is how a
  starting point is derived without guessing, and `clamp_to_band` keeps a
  refinement honest.

* Distributes a **group** allowance across its members by power, so the
  members' combined loudness equals the group target rather than each member
  reaching it.

* Verifies the result and reports the relationship it actually achieved.

The numbers (which offsets, which bands, which members) are channel taste
and belong in that channel's BRAND.md. This module holds none of them: a
channel states them in ONE fenced ``channel-mix`` block, which
`channel_mix_from_brand` parses and validates. There is no default band - a
water role solved without its channel's band is an error, never a fallback.

Usage
-----
    from lib.stem_balance import channel_mix_from_brand, measure_stem

    mix = channel_mix_from_brand(brand_text, source="Channels/<id>/BRAND.md")
    plan = mix.solve({"A1-music": -16.0, "A2-water": -9.9, ...})  # BUILT stems
    plan.gains_db                      # what to hand the mixer
    mix.check(plan.verify(remeasured)) # [] when every relationship is in band
    edit_decisions["metadata"]["mix_balance"] = mix.record(plan, verification)

Lower level, for a caller that builds its own spec:

    from lib.stem_balance import BalanceSpec, GroupSpec, solve_balance

    spec = BalanceSpec(
        reference_role="A1-music",
        master_target_lufs=-16.0,
        roles={"A2-water": 0.40},
        groups={
            "ambience": GroupSpec(
                prominence=0.20,
                members={"A3-forest": 0.70, "A4-birds": 0.20, "A5-wind": 0.10},
            )
        },
    )
    plan = solve_balance(spec, measured_built_lufs={...})
    plan.gains_db          # what to hand the mixer
    plan.verify(...)       # after the stems are re-measured
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from lib.ffmpeg_runtime import FFmpegNotAvailable, ffmpeg_path

logger = logging.getLogger(__name__)

#: A stem quieter than this is silence for practical purposes; deriving a gain
#: from it would produce a nonsensical boost.
SILENCE_FLOOR_LUFS = -70.0

#: A derived gain beyond this is a sign the stem or the spec is wrong, not
#: something to apply quietly.
MAX_ABS_GAIN_DB = 40.0


class StemBalanceError(ValueError):
    """Raised when a balance cannot be solved from the figures supplied."""


# --------------------------------------------------------------------------
# Specification
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSpec:
    """A set of roles whose *combined* level is what the brief constrains.

    `prominence` is the group's share of the reference, as an amplitude ratio.
    `members` are relative weights inside the group; only their ratio matters.
    """

    prominence: float
    members: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.members:
            raise StemBalanceError("a group needs at least one member role")
        if self.prominence <= 0:
            raise StemBalanceError("group prominence must be positive")
        if any(w <= 0 for w in self.members.values()):
            raise StemBalanceError("member weights must be positive")


@dataclass(frozen=True)
class BalanceSpec:
    """The creative relationship, as the channel states it.

    `reference_role` is the 100% layer. Its prominence is 1.0 by definition;
    "100%" means *reference*, not loud, and not maximised.
    """

    reference_role: str
    master_target_lufs: float
    roles: Mapping[str, float] = field(default_factory=dict)
    groups: Mapping[str, GroupSpec] = field(default_factory=dict)

    def all_roles(self) -> list[str]:
        roles = [self.reference_role, *self.roles]
        for group in self.groups.values():
            roles.extend(group.members)
        duplicates = {r for r in roles if roles.count(r) > 1}
        if duplicates:
            raise StemBalanceError(
                f"role(s) appear more than once in the spec: {sorted(duplicates)}"
            )
        return roles

    def group_of(self, role: str) -> Optional[str]:
        for name, group in self.groups.items():
            if role in group.members:
                return name
        return None


# --------------------------------------------------------------------------
# dB helpers
# --------------------------------------------------------------------------


def prominence_to_db(prominence: float) -> float:
    """A prominence percentage read as an amplitude ratio, in dB.

    100% -> 0 dB, 40% -> -7.96 dB, 20% -> -13.98 dB.
    """
    if prominence <= 0:
        raise StemBalanceError("prominence must be positive")
    return 20.0 * math.log10(prominence)


def clamp_to_band(offset_db: float, band: tuple[float, float]) -> float:
    """Keep a derived or refined offset inside a stated engineering band."""
    low, high = min(band), max(band)
    return max(low, min(high, offset_db))


def power_sum_lufs(levels: Iterable[float]) -> float:
    """Combine loudness figures by power, the way overlapping stems combine.

    Two equal stems sum ~3 dB hotter than either alone. Ignoring this is what
    makes a group of supporting layers overshoot its allowance.
    """
    total = sum(10.0 ** (level / 10.0) for level in levels if level > SILENCE_FLOOR_LUFS)
    if total <= 0:
        return SILENCE_FLOOR_LUFS
    return 10.0 * math.log10(total)


def distribute_group(group_target_lufs: float, weights: Mapping[str, float]) -> dict[str, float]:
    """Split a group's loudness allowance across its members by power.

    The members' power sum equals `group_target_lufs`; it is not handed to each
    of them. That difference is the whole point of grouping.
    """
    total_weight = sum(weights.values())
    return {
        role: group_target_lufs + 10.0 * math.log10(weight / total_weight)
        for role, weight in weights.items()
    }


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def _ffmpeg() -> str:
    """Resolve `ffmpeg` through PATH, as OpenMontage's tools do."""
    try:
        return ffmpeg_path()
    except FFmpegNotAvailable as exc:
        raise StemBalanceError(str(exc)) from exc


@dataclass
class StemMeasurement:
    """What `ebur128` reports for one assembled stem."""

    path: str
    integrated_lufs: float
    true_peak_dbtp: float
    lra_lu: float
    short_term_max_lufs: Optional[float] = None
    short_term_min_lufs: Optional[float] = None

    @property
    def short_term_span_lu(self) -> Optional[float]:
        if self.short_term_max_lufs is None or self.short_term_min_lufs is None:
            return None
        return round(self.short_term_max_lufs - self.short_term_min_lufs, 2)


def measure_stem(path: str | Path, *, short_term: bool = True) -> StemMeasurement:
    """Measure an ASSEMBLED stem file with `ebur128`.

    Call this on the stem as it will appear in the mix — concatenated,
    crossfaded, treated. Measuring the sources instead is the regression this
    module exists to prevent.

    `short_term` also reports the loudest and quietest 3-second windows, which
    is how a transient problem (a bird spike, a wind gust) shows up in a stem
    whose integrated figure looks correct.
    """
    path = Path(path)
    if not path.is_file():
        raise StemBalanceError(f"no such stem: {path}")

    result = subprocess.run(
        [
            _ffmpeg(),
            "-nostats",
            "-hide_banner",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    log = result.stderr

    summary = log.rsplit("Summary:", 1)
    tail = summary[-1] if len(summary) > 1 else log

    def _grab(pattern: str) -> Optional[float]:
        match = re.search(pattern, tail)
        return float(match.group(1)) if match else None

    integrated = _grab(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
    true_peak = _grab(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
    lra = _grab(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU")

    if integrated is None:
        raise StemBalanceError(
            f"ebur128 reported no integrated loudness for {path.name}; "
            "the file may be silent or unreadable"
        )

    short_max = short_min = None
    if short_term:
        values = [
            float(v)
            for v in re.findall(r"\sS:\s*(-?\d+(?:\.\d+)?)\s", log)
            if float(v) > SILENCE_FLOOR_LUFS
        ]
        if values:
            short_max, short_min = max(values), min(values)

    return StemMeasurement(
        path=str(path),
        integrated_lufs=round(integrated, 2),
        true_peak_dbtp=round(true_peak, 2) if true_peak is not None else float("nan"),
        lra_lu=round(lra, 2) if lra is not None else float("nan"),
        short_term_max_lufs=round(short_max, 2) if short_max is not None else None,
        short_term_min_lufs=round(short_min, 2) if short_min is not None else None,
    )


# --------------------------------------------------------------------------
# Solving
# --------------------------------------------------------------------------


@dataclass
class RolePlan:
    """The decision for one role, with the figures it was derived from."""

    role: str
    group: Optional[str]
    prominence: Optional[float]
    offset_from_reference_db: float
    built_stem_lufs: float
    target_lufs: float
    gain_db: float


@dataclass
class BalancePlan:
    """A complete, reproducible balance decision."""

    reference_role: str
    reference_target_lufs: float
    roles: dict[str, RolePlan]
    group_targets_lufs: dict[str, float]
    predicted_sum_lufs: float
    master_target_lufs: float
    notes: list[str] = field(default_factory=list)

    @property
    def gains_db(self) -> dict[str, float]:
        """What to hand the mixer, per role."""
        return {role: plan.gain_db for role, plan in self.roles.items()}

    @property
    def targets_lufs(self) -> dict[str, float]:
        return {role: plan.target_lufs for role, plan in self.roles.items()}

    def to_metadata(self) -> dict[str, Any]:
        """Shape for `edit_decisions.metadata.mix_balance`.

        Deliberately records the method and the built-stem figure per role, so
        a later episode reproduces the balance instead of rediscovering it —
        and so a reviewer can see the gain was not a provider default.
        """
        return {
            "method": (
                "creative prominence read as an amplitude ratio "
                "(offset_db = 20*log10(prominence)), applied relative to the "
                "reference role; supporting roles solved as a GROUP by power "
                "so their combined level meets the group allowance; per-role "
                "gain = target - MEASURED BUILT STEM loudness"
            ),
            "reference_role": self.reference_role,
            "reference_target_lufs": round(self.reference_target_lufs, 2),
            "master_target_lufs": self.master_target_lufs,
            "predicted_sum_lufs": round(self.predicted_sum_lufs, 2),
            "group_targets_lufs": {
                k: round(v, 2) for k, v in self.group_targets_lufs.items()
            },
            "roles": {
                role: {
                    "group": plan.group,
                    "prominence_of_reference": plan.prominence,
                    "offset_from_reference_db": round(plan.offset_from_reference_db, 2),
                    "built_stem_lufs": round(plan.built_stem_lufs, 2),
                    "target_lufs": round(plan.target_lufs, 2),
                    "gain_db": round(plan.gain_db, 2),
                }
                for role, plan in self.roles.items()
            },
            "notes": self.notes,
        }

    def verify(
        self,
        remeasured_lufs: Mapping[str, float],
        *,
        tolerance_lu: float = 1.0,
    ) -> "BalanceVerification":
        """Check the executed mix against the plan.

        Pass the loudness of each stem measured AFTER its gain was applied.
        The relationship is checked, not just the individual numbers: a mix
        where every role hits its target but the group sums 5 dB hot is still
        the defect this module exists to prevent.
        """
        role_errors: dict[str, float] = {}
        for role, plan in self.roles.items():
            if role not in remeasured_lufs:
                continue
            role_errors[role] = round(remeasured_lufs[role] - plan.target_lufs, 2)

        reference_actual = remeasured_lufs.get(
            self.reference_role, self.reference_target_lufs
        )

        achieved: dict[str, float] = {}
        for role in self.roles:
            if role == self.reference_role or role not in remeasured_lufs:
                continue
            achieved[role] = round(remeasured_lufs[role] - reference_actual, 2)

        group_achieved: dict[str, float] = {}
        for group, target in self.group_targets_lufs.items():
            members = [
                remeasured_lufs[role]
                for role, plan in self.roles.items()
                if plan.group == group and role in remeasured_lufs
            ]
            if members:
                group_achieved[group] = round(
                    power_sum_lufs(members) - reference_actual, 2
                )

        failures = [
            f"{role}: {error:+.2f} LU from its target"
            for role, error in role_errors.items()
            if abs(error) > tolerance_lu
        ]

        return BalanceVerification(
            role_errors_lu=role_errors,
            achieved_offsets_db=achieved,
            achieved_group_offsets_db=group_achieved,
            tolerance_lu=tolerance_lu,
            failures=failures,
        )


@dataclass
class BalanceVerification:
    """The relationship the mix actually achieved."""

    role_errors_lu: dict[str, float]
    achieved_offsets_db: dict[str, float]
    achieved_group_offsets_db: dict[str, float]
    tolerance_lu: float
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures

    def in_band(self, group: str, band: tuple[float, float]) -> bool:
        """Is a group's achieved offset inside its stated engineering band?

        Both edges count. A group quieter than its band is a defect too: on
        this installation birds 42 dB under the music were simply inaudible.
        """
        if group not in self.achieved_group_offsets_db:
            return False
        low, high = min(band), max(band)
        value = self.achieved_group_offsets_db[group]
        return low - self.tolerance_lu <= value <= high + self.tolerance_lu

    def to_metadata(self) -> dict[str, Any]:
        return {
            "role_errors_lu": self.role_errors_lu,
            "achieved_offsets_from_reference_db": self.achieved_offsets_db,
            "achieved_group_offsets_from_reference_db": self.achieved_group_offsets_db,
            "tolerance_lu": self.tolerance_lu,
            "measurement_passed": self.passed,
            "failures": self.failures,
            "limitation": (
                "These are MEASUREMENTS. They establish the balance is the one "
                "that was decided; they do not establish that it sounds good. "
                "A subjective listening review is a separate check and must be "
                "reported separately."
            ),
        }


def solve_balance(
    spec: BalanceSpec,
    measured_built_lufs: Mapping[str, float],
    *,
    water_role: Optional[str] = None,
    water_band: Optional[tuple[float, float]] = None,
    group_bands: Optional[Mapping[str, tuple[float, float]]] = None,
) -> BalancePlan:
    """Derive per-role targets and gains from measured built stems.

    `measured_built_lufs` must come from the ASSEMBLED stems (see
    `measure_stem`). Supplying source-file means reintroduces the error this
    module documents.

    The reference role's target is chosen so the predicted sum of all stems
    lands on `master_target_lufs`. That way the balance is set by the
    relationship and the overall level follows from it — rather than the
    reverse, where final normalisation is asked to repair a balance it cannot
    reach inside.
    """
    group_bands = dict(group_bands or {})
    notes: list[str] = []
    if water_role and water_band is None:
        # There used to be a built-in default band here. It silently restored
        # a generic relationship the channel had long since replaced.
        raise StemBalanceError(
            f"water role {water_role!r} was given without its band. The band is "
            "channel taste: read it from the channel's BRAND.md "
            "(channel_mix_from_brand) - there is no default."
        )

    roles = spec.all_roles()
    missing = [r for r in roles if r not in measured_built_lufs]
    if missing:
        raise StemBalanceError(
            f"no measured built-stem loudness for: {sorted(missing)}. "
            "Assemble and measure every stem before solving the balance."
        )

    silent = [
        r for r in roles if measured_built_lufs[r] <= SILENCE_FLOOR_LUFS
    ]
    if silent:
        raise StemBalanceError(
            f"stem(s) measure at or below the silence floor: {sorted(silent)}. "
            "A gain cannot be derived from silence; fix the stem first."
        )

    # --- offsets relative to the reference, before any level is chosen ---
    offsets: dict[str, float] = {spec.reference_role: 0.0}
    prominences: dict[str, Optional[float]] = {spec.reference_role: 1.0}
    role_group: dict[str, Optional[str]] = {spec.reference_role: None}

    for role, prominence in spec.roles.items():
        offset = prominence_to_db(prominence)
        if water_role and role == water_role:
            clamped = clamp_to_band(offset, water_band)
            if abs(clamped - offset) > 0.01:
                notes.append(
                    f"{role}: derived offset {offset:.2f} dB clamped to "
                    f"{clamped:.2f} dB by the stated band {water_band}"
                )
            offset = clamped
        offsets[role] = offset
        prominences[role] = prominence
        role_group[role] = None

    group_offsets: dict[str, float] = {}
    for group_name, group in spec.groups.items():
        offset = prominence_to_db(group.prominence)
        band = group_bands.get(group_name)
        if band:
            clamped = clamp_to_band(offset, band)
            if abs(clamped - offset) > 0.01:
                notes.append(
                    f"group {group_name}: derived offset {offset:.2f} dB "
                    f"clamped to {clamped:.2f} dB by the stated band {band}"
                )
            offset = clamped
        group_offsets[group_name] = offset

        # The group allowance is split by power across its members, so their
        # COMBINED level meets the allowance.
        member_offsets = distribute_group(offset, group.members)
        for role, member_offset in member_offsets.items():
            offsets[role] = member_offset
            prominences[role] = None
            role_group[role] = group_name
        notes.append(
            f"group {group_name}: allowance {offset:.2f} dB split by power "
            "across "
            + ", ".join(
                f"{role} {member_offsets[role] - offset:+.2f} dB"
                for role in group.members
            )
            + " - the members SUM to the allowance rather than each reaching it"
        )

    # --- choose the reference level so the whole mix lands on target ---
    # Sum of relative powers, then solve for the reference's absolute level.
    relative_sum_db = power_sum_lufs(offsets.values())
    reference_target = spec.master_target_lufs - relative_sum_db

    role_plans: dict[str, RolePlan] = {}
    for role in roles:
        target = reference_target + offsets[role]
        gain = target - measured_built_lufs[role]
        if abs(gain) > MAX_ABS_GAIN_DB:
            raise StemBalanceError(
                f"{role} needs {gain:+.1f} dB to reach {target:.1f} LUFS from a "
                f"built stem measuring {measured_built_lufs[role]:.1f} LUFS. "
                "A correction that large means the stem or the spec is wrong; "
                "do not apply it quietly."
            )
        role_plans[role] = RolePlan(
            role=role,
            group=role_group[role],
            prominence=prominences[role],
            offset_from_reference_db=offsets[role],
            built_stem_lufs=measured_built_lufs[role],
            target_lufs=target,
            gain_db=round(gain, 2),
        )

    return BalancePlan(
        reference_role=spec.reference_role,
        reference_target_lufs=reference_target,
        roles=role_plans,
        group_targets_lufs={
            name: reference_target + offset for name, offset in group_offsets.items()
        },
        predicted_sum_lufs=power_sum_lufs(
            plan.target_lufs for plan in role_plans.values()
        ),
        master_target_lufs=spec.master_target_lufs,
        notes=notes,
    )


# --------------------------------------------------------------------------
# The channel's own mix settings (one ``channel-mix`` block in BRAND.md)
# --------------------------------------------------------------------------

_MIX_BLOCK = re.compile(r"^```channel-mix[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
_TOP_KEYS = {"reference_role", "master_target_lufs", "water", "supporting_group", "approved"}
_WATER_KEYS = {"role", "offset_db", "band_db", "treatment_af"}
_GROUP_KEYS = {"name", "offset_db", "band_db", "members"}
#: Offsets are below the reference, and a mix a listener can hear sits well
#: inside this range. Anything outside it is a typo, not taste.
_OFFSET_LIMITS_DB = (-60.0, 0.0)
_MASTER_LIMITS_LUFS = (-40.0, -6.0)


class ChannelMixError(StemBalanceError):
    """The channel's ``channel-mix`` block is missing, duplicated or invalid."""


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ChannelMixError(f"{where} must be a number, got {value!r}")
    return float(value)


def _band(value: Any, offset: float, where: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ChannelMixError(f"{where}.band_db must be [low, high]")
    low, high = (_number(v, f"{where}.band_db") for v in value)
    if not low < high:
        raise ChannelMixError(f"{where}.band_db must be [low, high] with low < high, got {value}")
    if not (_OFFSET_LIMITS_DB[0] <= low and high <= _OFFSET_LIMITS_DB[1]):
        raise ChannelMixError(f"{where}.band_db {value} is outside {_OFFSET_LIMITS_DB} dB")
    if not low <= offset <= high:
        raise ChannelMixError(
            f"{where}.offset_db {offset} is outside its own band {value}; the block "
            "contradicts itself")
    return low, high


def _exact_keys(data: Any, allowed: set[str], required: set[str], where: str) -> Mapping:
    if not isinstance(data, Mapping):
        raise ChannelMixError(f"{where} must be a mapping")
    unknown = set(data) - allowed
    if unknown:
        raise ChannelMixError(f"{where} has unknown key(s) {sorted(unknown)} - a typo would "
                              "otherwise be ignored silently")
    missing = required - set(data)
    if missing:
        raise ChannelMixError(f"{where} is missing required key(s) {sorted(missing)}")
    return data


@dataclass(frozen=True)
class ChannelMix:
    """A channel's approved layer relationship, exactly as its BRAND.md states it."""

    reference_role: str
    master_target_lufs: float
    water_role: str
    water_offset_db: float
    water_band_db: tuple[float, float]
    water_treatment_af: Optional[str]
    group_name: Optional[str]
    group_offset_db: Optional[float]
    group_band_db: Optional[tuple[float, float]]
    members: Mapping[str, float]
    approved: Mapping[str, Any]
    block_sha256: str
    source: Optional[str] = None
    source_sha256: Optional[str] = None

    # ---- solving ----------------------------------------------------------

    def spec(self, present_roles: Optional[Iterable[str]] = None) -> BalanceSpec:
        """The `BalanceSpec` for the stems that actually exist.

        The reference and the water role are required. A group member with no
        stem is dropped and the remaining members share the WHOLE group
        allowance in their stated ratio - which is how the block's weights are
        defined, not an adjustment.
        """
        present = None if present_roles is None else set(present_roles)
        for role in (self.reference_role, self.water_role):
            if present is not None and role not in present:
                raise ChannelMixError(f"required role {role!r} has no stem")
        groups: dict[str, GroupSpec] = {}
        if self.group_name:
            members = {r: w for r, w in self.members.items() if present is None or r in present}
            if members:
                groups[self.group_name] = GroupSpec(
                    prominence=10 ** (self.group_offset_db / 20.0), members=members)
        return BalanceSpec(
            reference_role=self.reference_role,
            master_target_lufs=self.master_target_lufs,
            roles={self.water_role: 10 ** (self.water_offset_db / 20.0)},
            groups=groups,
        )

    def solve(self, measured_built_lufs: Mapping[str, float]) -> BalancePlan:
        """Solve from MEASURED BUILT stems, with the channel's bands enforced."""
        spec = self.spec(measured_built_lufs)
        plan = solve_balance(
            spec, measured_built_lufs, water_role=self.water_role,
            water_band=self.water_band_db,
            group_bands={self.group_name: self.group_band_db} if spec.groups else None,
        )
        omitted = sorted(r for r in self.members if r not in measured_built_lufs)
        if omitted:
            plan.notes.append(f"no stem for {omitted}; the remaining group members share "
                              "the whole group allowance in their stated ratio")
        return plan

    def check(self, verification: BalanceVerification) -> list[str]:
        """Every achieved relationship outside the channel's bands (both edges)."""
        failures = list(verification.failures)
        water = verification.achieved_offsets_db.get(self.water_role)
        low, high = self.water_band_db
        tol = verification.tolerance_lu
        if water is None:
            failures.append(f"{self.water_role}: not re-measured")
        elif not low - tol <= water <= high + tol:
            failures.append(f"{self.water_role}: {water:+.2f} dB from the reference is outside "
                            f"the channel band [{low}, {high}]")
        if self.group_name and self.group_name in verification.achieved_group_offsets_db:
            if not verification.in_band(self.group_name, self.group_band_db):
                failures.append(
                    f"group {self.group_name}: "
                    f"{verification.achieved_group_offsets_db[self.group_name]:+.2f} dB is outside "
                    f"the channel band {list(self.group_band_db)}")
        return failures

    # ---- recording ----------------------------------------------------------

    def to_metadata(self) -> dict[str, Any]:
        return {
            "source": self.source, "source_sha256": self.source_sha256,
            "block_sha256": self.block_sha256,
            "reference_role": self.reference_role,
            "master_target_lufs": self.master_target_lufs,
            "water": {"role": self.water_role, "offset_db": self.water_offset_db,
                      "band_db": list(self.water_band_db),
                      "treatment_af": self.water_treatment_af},
            "supporting_group": None if not self.group_name else {
                "name": self.group_name, "offset_db": self.group_offset_db,
                "band_db": list(self.group_band_db), "members": dict(self.members)},
            "approved": dict(self.approved),
        }

    def record(self, plan: BalancePlan,
               verification: Optional[BalanceVerification] = None) -> dict[str, Any]:
        """The ``edit_decisions.metadata.mix_balance`` record for this mix."""
        record = plan.to_metadata()
        record["channel_mix"] = self.to_metadata()
        if verification is not None:
            record["verification"] = verification.to_metadata()
            record["channel_band_failures"] = self.check(verification)
        return record


def channel_mix_from_brand(brand_text: str, *, source: Optional[str] = None) -> ChannelMix:
    """Parse and validate the ONE ``channel-mix`` block in a channel's BRAND.md.

    Raises `ChannelMixError` when the block is missing or duplicated, has an
    unknown or missing key, a band that is inverted or out of range, an
    offset outside its own band, a non-positive or duplicated member, or a
    role used twice. Nothing is defaulted.
    """
    import yaml

    blocks = _MIX_BLOCK.findall(brand_text)
    if len(blocks) != 1:
        raise ChannelMixError(
            f"BRAND.md must contain exactly one ```channel-mix block; found {len(blocks)}")
    block = blocks[0]
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise ChannelMixError(f"the channel-mix block is not valid YAML: {exc}") from exc
    data = _exact_keys(data, _TOP_KEYS, {"reference_role", "master_target_lufs", "water"},
                       "channel-mix")
    reference = data["reference_role"]
    if not isinstance(reference, str) or not reference.strip():
        raise ChannelMixError("reference_role must be a role name")
    master = _number(data["master_target_lufs"], "master_target_lufs")
    if not _MASTER_LIMITS_LUFS[0] <= master <= _MASTER_LIMITS_LUFS[1]:
        raise ChannelMixError(f"master_target_lufs {master} is outside {_MASTER_LIMITS_LUFS}")

    water = _exact_keys(data["water"], _WATER_KEYS, {"role", "offset_db", "band_db"}, "water")
    water_offset = _number(water["offset_db"], "water.offset_db")
    water_band = _band(water["band_db"], water_offset, "water")
    treatment = water.get("treatment_af")
    if treatment is not None and (not isinstance(treatment, str) or not treatment.strip()):
        raise ChannelMixError("water.treatment_af must be an FFmpeg filter string when given")

    group_name = group_offset = group_band = None
    members: dict[str, float] = {}
    if data.get("supporting_group") is not None:
        group = _exact_keys(data["supporting_group"], _GROUP_KEYS, _GROUP_KEYS,
                            "supporting_group")
        group_name = str(group["name"])
        group_offset = _number(group["offset_db"], "supporting_group.offset_db")
        group_band = _band(group["band_db"], group_offset, "supporting_group")
        if not isinstance(group["members"], Mapping) or not group["members"]:
            raise ChannelMixError("supporting_group.members must name at least one role")
        for role, weight in group["members"].items():
            weight = _number(weight, f"supporting_group.members.{role}")
            if weight <= 0:
                raise ChannelMixError(f"member weight for {role!r} must be positive")
            members[str(role)] = weight
        if group_offset > water_offset:
            raise ChannelMixError(
                f"the supporting group ({group_offset} dB) is set louder than the principal "
                f"water ({water_offset} dB); the block is inconsistent")

    roles = [reference, water["role"], *members]
    duplicates = sorted({r for r in roles if roles.count(r) > 1})
    if duplicates:
        raise ChannelMixError(f"role(s) used more than once: {duplicates}")
    approved = data.get("approved") or {}
    if not isinstance(approved, Mapping):
        raise ChannelMixError("approved must be a mapping when given")
    # YAML reads an unquoted date as a date object; the record must stay JSON
    # (it is written into edit_decisions and the checkpoint).
    approved = {str(k): v.isoformat() if hasattr(v, "isoformat") else v
                for k, v in approved.items()}
    if any(not isinstance(v, (str, int, float, bool, type(None))) for v in approved.values()):
        raise ChannelMixError("approved values must be plain text, numbers or dates")

    return ChannelMix(
        reference_role=reference, master_target_lufs=master,
        water_role=str(water["role"]), water_offset_db=water_offset, water_band_db=water_band,
        water_treatment_af=treatment, group_name=group_name, group_offset_db=group_offset,
        group_band_db=group_band, members=members, approved=dict(approved),
        block_sha256=hashlib.sha256(block.encode("utf-8")).hexdigest(),
        source=source,
        source_sha256=hashlib.sha256(brand_text.encode("utf-8")).hexdigest(),
    )


def channel_mix_from_file(path: str | Path) -> ChannelMix:
    """`channel_mix_from_brand` for a BRAND.md on disk, recording its path and hash."""
    path = Path(path)
    return channel_mix_from_brand(path.read_text(encoding="utf-8"), source=str(path))


def check_mix_record(edit_decisions: Mapping[str, Any], mix: ChannelMix) -> list[str]:
    """Blockers when an edit's recorded mix was not solved from THIS channel mix.

    Requires ``edit_decisions.metadata.mix_balance`` to carry the parsed block
    (matching hash and values), per-role offsets equal to what the block
    yields, and a verification with no channel-band failure.
    """
    record = ((edit_decisions.get("metadata") or {}).get("mix_balance") or {})
    blockers: list[str] = []
    recorded = record.get("channel_mix")
    if not recorded:
        return ["edit_decisions.metadata.mix_balance.channel_mix is missing - the mix was not "
                "solved from the channel's BRAND.md block"]
    if recorded.get("block_sha256") != mix.block_sha256:
        blockers.append("the recorded channel-mix block differs from the channel's current "
                        "BRAND.md block (hash mismatch) - re-solve the mix")
    if recorded != mix.to_metadata() and recorded.get("block_sha256") == mix.block_sha256:
        stored = {k: v for k, v in recorded.items() if k not in ("source", "source_sha256")}
        current = {k: v for k, v in mix.to_metadata().items() if k not in ("source", "source_sha256")}
        if stored != current:
            blockers.append("the recorded channel-mix values differ from the parsed block")
    roles = record.get("roles") or {}
    present = [r for r in roles]
    try:
        expected = solve_balance(
            mix.spec(present), {r: v["built_stem_lufs"] for r, v in roles.items()},
            water_role=mix.water_role, water_band=mix.water_band_db,
            group_bands={mix.group_name: mix.group_band_db} if mix.group_name else None)
        for role, plan in expected.roles.items():
            got = roles.get(role, {}).get("offset_from_reference_db")
            if got is None or abs(got - round(plan.offset_from_reference_db, 2)) > 0.05:
                blockers.append(f"{role}: recorded offset {got} is not the channel's "
                                f"{plan.offset_from_reference_db:.2f} dB")
    except (StemBalanceError, KeyError, TypeError) as exc:
        blockers.append(f"the recorded mix cannot be re-derived from the channel block: {exc}")
    verification = record.get("verification")
    if not verification:
        blockers.append("the executed mix was not re-measured and verified")
    elif record.get("channel_band_failures"):
        blockers.append("the verified mix is outside the channel's bands: "
                        + "; ".join(record["channel_band_failures"]))
    return blockers


def _main(argv: list[str]) -> int:
    """Measure stems given on the command line."""
    if not argv:
        print(__doc__)
        return 2
    out = [measure_stem(path).__dict__ for path in argv]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
