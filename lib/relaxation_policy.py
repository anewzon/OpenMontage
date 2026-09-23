"""Duration and chunking policy for the relaxation pipeline.

The numbers live in `pipeline_defs/relaxation.yaml` under `metadata.duration`
and `metadata.chunking`. This module reads them from there rather than
restating them, so the manifest stays the single source of truth and a test
cannot pass against a policy the manifest no longer declares.

Why a module rather than prose: "validate 60 <= target_duration_seconds <=
18000" in a Director is an instruction an agent may skip. A function that
raises is a contract that can be tested.

    from lib.relaxation_policy import validate_duration, chunking_required

    validate_duration(900)          # ok
    validate_duration(59)           # DurationOutOfRange
    chunking_required(900)          # False - 15 minutes, chunking optional
    chunking_required(1800)         # True  - 30 minutes, chunk plan required

It also holds the paid-audio budget contract: the proposal-time estimate and
the approved-budget cap every paid call runs under. Pricing is never restated
here - every figure comes from the provider tool's own `estimate_cost()` - and
the cap is OpenMontage's own `CostTracker` in `cap` mode.

    plan = plan_paid_audio(target_duration_seconds=120, music={...}, sfx={...})
    plan["cost_estimate"]          # -> proposal_packet.cost_estimate
    print(budget_summary(plan))    # what the operator approves
    tracker = approved_budget_tracker(proposal_packet, project_dir)
    tracker.run_tool(tool, inputs, operation="...")   # estimate/reserve/execute/reconcile

Paid music runs through `generate_music_programme`, which authorises every
request from the approved proposal and the project's durable records, screens
every candidate with bounded evidence, and stops for the operator whenever a
result is all-rejected, uncertain or would draw on the retry allowance.
"""

from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError, CostTracker

__all__ = [
    "ApprovedBudgetTracker",
    "BudgetNotApproved",
    "PaidAudioInProgress",
    "approved_sfx_limits",
    "generate_sfx_source",
    "load_sfx_ledger",
    "paid_audio_in_progress",
    "reconcile_sfx_progress",
    "record_sfx_review",
    "MusicLimitsUnavailable",
    "account_music_candidates",
    "approved_music_limits",
    "authorize_music_request",
    "decide_music_candidate",
    "find_prohibited_terms",
    "generate_music_programme",
    "generation_duration_strategy",
    "load_music_ledger",
    "next_music_request",
    "reconcile_music_progress",
    "record_music_review",
    "screen_music_candidate",
    "DurationOutOfRange",
    "PaidCostUnavailable",
    "approved_budget_tracker",
    "budget_summary",
    "plan_paid_audio",
    "chunking_required",
    "duration_bounds",
    "chunk_threshold_seconds",
    "load_manifest",
    "validate_duration",
]

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "pipeline_defs" / "relaxation.yaml"


class DurationOutOfRange(ValueError):
    """Raised when a requested duration falls outside the supported range.

    Deliberately an error rather than a clamp. A 5-hour request that quietly
    becomes 3 hours, or a 30-second request that quietly becomes 60, is a
    production the operator did not ask for.
    """


@functools.lru_cache(maxsize=1)
def load_manifest(path: Optional[str] = None) -> Mapping[str, Any]:
    """Parse the relaxation manifest once."""
    return yaml.safe_load(Path(path or MANIFEST_PATH).read_text(encoding="utf-8"))


def _policy(manifest: Optional[Mapping[str, Any]], key: str) -> Mapping[str, Any]:
    data = (manifest or load_manifest()).get("metadata", {}).get(key)
    if not data:
        raise RuntimeError(
            f"relaxation.yaml no longer declares metadata.{key}; the pipeline's "
            f"{key} policy has no source of truth"
        )
    return data


def duration_bounds(manifest: Optional[Mapping[str, Any]] = None) -> tuple[int, int]:
    """(min_seconds, max_seconds) the pipeline supports."""
    policy = _policy(manifest, "duration")
    return int(policy["min_seconds"]), int(policy["max_seconds"])


def chunk_threshold_seconds(manifest: Optional[Mapping[str, Any]] = None) -> int:
    """Timeline length at which a chunk plan becomes required."""
    return int(_policy(manifest, "chunking")["threshold_seconds"])


def validate_duration(
    target_duration_seconds: float,
    manifest: Optional[Mapping[str, Any]] = None,
) -> float:
    """Return the duration, or raise `DurationOutOfRange`.

    Call this at the proposal stage. Surfacing the problem is the point; do not
    catch this and substitute a nearby value.
    """
    low, high = duration_bounds(manifest)
    if not low <= target_duration_seconds <= high:
        raise DurationOutOfRange(
            f"target_duration_seconds={target_duration_seconds} is outside the "
            f"supported range [{low}, {high}] "
            f"({low / 60:.0f} minute(s) to {high / 3600:.0f} hours). "
            "Surface this to the operator; do not clamp it."
        )
    return float(target_duration_seconds)


def chunking_required(
    timeline_seconds: float,
    manifest: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when `metadata.chunk_plan[]` is mandatory for this timeline.

    Below the threshold chunking is *optional* — permitted when render
    complexity calls for it, but never imposed. A 60-second piece does not get
    a chunk plan.
    """
    return timeline_seconds > chunk_threshold_seconds(manifest)


# --------------------------------------------------------------------------
# Paid-audio budget policy
# --------------------------------------------------------------------------
#
# Channel-neutral on purpose. How much unique music a production needs, which
# SFX an episode needs and how generous the retry allowance is are all inputs:
# they come from the channel's BRAND.md, the approved concept and the actual
# footage. Nothing below knows a channel, a subject or a sound category.

#: Approval states under which paid generation may proceed.
_APPROVED = ("approved", "approved_with_changes")


class PaidCostUnavailable(RuntimeError):
    """A paid operation cannot be priced safely, so it must not be planned."""


class BudgetNotApproved(RuntimeError):
    """No operator-approved paid budget exists for this production."""


def _tool(name: str) -> Any:
    from tools.tool_registry import registry

    registry.ensure_discovered()
    tool = registry.get(name)
    if tool is None:
        raise PaidCostUnavailable(f"tool {name!r} is not registered")
    return tool


def _unit_cost(tool: Any, inputs: Mapping[str, Any]) -> float:
    try:
        return float(tool.estimate_cost(dict(inputs)))
    except Exception as exc:  # the tool's own reason says what to confirm
        raise PaidCostUnavailable(
            f"{tool.name} cannot be priced: {exc} Paid production is blocked "
            "until this is resolved - never make an unpriced call to find out."
        ) from exc


def _require_fraction(value: Any, name: str) -> float:
    if value is None:
        raise ValueError(f"{name} must be stated explicitly; there is no default")
    value = float(value)
    if not 0 <= value <= 2:
        raise ValueError(f"{name} must be within [0, 2]")
    return value


def plan_paid_audio(
    *,
    target_duration_seconds: float,
    music: Optional[Mapping[str, Any]] = None,
    sfx: Optional[Mapping[str, Any]] = None,
    video_sourcing: str = "licensed_manual",
    budget_cap_usd: Optional[float] = None,
) -> dict[str, Any]:
    """Estimate every paid audio generation BEFORE anything is spent.

    ``music`` (omit for a production without generated music)::

        tool                        registry name of the music provider
        tool_inputs                 inputs priced per generation (model, ...)
        unique_music_seconds        accepted unique programme to generate
        seconds_per_generation      optional requested length of each call;
                                    derived when omitted (see below)
        generation_duration_reason  required when a shorter-than-efficient
                                    length is chosen on fixed pricing
        candidates_per_generation   candidates one paid request returns
        accepted_per_generation     candidates expected to pass selection
        retry_allowance             extra requests, as a fraction

    ``sfx`` (omit when native audio covers the episode)::

        tool                        registry name of the SFX provider
        tool_inputs                 inputs shared by every source (model, ...)
        sources                     [{purpose, duration_seconds, count}]
        retry_allowance             extra generations, as a fraction

    Music is planned as a PROGRAMME, not minute-for-minute: unique seconds are
    the input, and the runtime beyond them is reprised in the edit. When
    ``seconds_per_generation`` is omitted it is derived from the tool's own
    contract (`generation_duration_strategy`): on fixed per-generation pricing
    the longest permitted request is chosen, because it minimises paid calls.
    The requested length only sizes the estimate - after generation, only the
    MEASURED length of accepted candidates counts (`account_music_candidates`).
    Generated SFX is planned in SOURCE seconds; long beds are built from
    loopable sources.

    Returns ``cost_estimate`` (schema-valid for ``proposal_packet``) and
    ``metadata`` (the arithmetic, for ``proposal_packet.metadata.paid_audio_plan``).
    Raises `PaidCostUnavailable` when any paid line cannot be priced.
    """
    validate_duration(target_duration_seconds)
    line_items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"target_duration_seconds": float(target_duration_seconds)}

    if music:
        tool = _tool(music["tool"])
        unique = float(music["unique_music_seconds"])
        if not 0 < unique <= float(target_duration_seconds):
            raise ValueError(
                "unique_music_seconds must be > 0 and no longer than the production"
            )
        base_inputs = dict(music.get("tool_inputs") or {})
        strategy = _duration_choice(tool, base_inputs, music)
        per_gen = strategy["selected_seconds"]
        candidates = int(music["candidates_per_generation"])
        accepted = float(music["accepted_per_generation"])
        if per_gen <= 0 or candidates < 1 or not 0 < accepted <= candidates:
            raise ValueError("music generation parameters are inconsistent")
        allowance = _require_fraction(music.get("retry_allowance"), "music.retry_allowance")
        inputs = {**base_inputs, "duration_seconds": per_gen}
        unit = _unit_cost(tool, inputs)
        base = math.ceil(unique / (per_gen * accepted))
        retry = math.ceil(base * allowance)
        line_items.append({
            "tool": tool.name, "operation": "music generation", "quantity": base,
            "estimated_usd": round(base * unit, 4),
            "notes": f"{unique:.0f}s accepted unique programme / ({per_gen:.0f}s x "
                     f"{accepted:g} accepted per request); {candidates} candidates each",
        })
        if retry:
            line_items.append({
                "tool": tool.name, "operation": "music retry/rejection allowance",
                "quantity": retry, "estimated_usd": round(retry * unit, 4),
                "notes": f"{allowance:.0%} of base requests; every retry is a new paid request",
            })
        models = (tool.get_info().get("models") or {})
        meta["music"] = {
            "tool": tool.name, "provider": tool.provider,
            "model": inputs.get("model") or models.get("default"),
            "unique_music_seconds": unique,
            "reprised_seconds": round(float(target_duration_seconds) - unique, 3),
            "seconds_per_generation": per_gen,
            "generation_strategy": strategy,
            "candidates_per_generation": candidates,
            "accepted_per_generation": accepted,
            "base_requests": base,
            "retry_requests": retry,
            "usd_per_request": unit,
            "estimated_usd": round((base + retry) * unit, 4),
        }

    if sfx:
        tool = _tool(sfx["tool"])
        shared = dict(sfx.get("tool_inputs") or {})
        sources = [dict(s) for s in (sfx.get("sources") or [])]
        if not sources:
            raise ValueError("sfx.sources is empty; omit sfx instead")
        allowance = _require_fraction(sfx.get("retry_allowance"), "sfx.retry_allowance")
        count = 0
        seconds = 0.0
        base_usd = 0.0
        dearest = 0.0
        for source in sources:
            n = int(source.get("count", 1))
            d = source.get("duration_seconds")
            priced = {**shared, "duration_seconds": d}
            unit = _unit_cost(tool, priced)
            count += n
            seconds += n * float(tool.billable_seconds(priced))
            base_usd += n * unit
            dearest = max(dearest, unit)
        retry = math.ceil(count * allowance)
        line_items.append({
            "tool": tool.name, "operation": "SFX generation", "quantity": count,
            "estimated_usd": round(base_usd, 4),
            "notes": f"{seconds:.1f}s of generated source across {len(sources)} "
                     "episode-specific sources; long beds built from loops, not "
                     "minute-for-minute",
        })
        if retry:
            line_items.append({
                "tool": tool.name, "operation": "SFX retry allowance", "quantity": retry,
                "estimated_usd": round(retry * dearest, 4),
                "notes": f"{allowance:.0%} of generations, priced at the dearest source",
            })
        meta["sfx"] = {
            "tool": tool.name, "provider": tool.provider,
            "model": shared.get("model_id"),
            "generations": count,
            "retry_generations": retry,
            "generated_source_seconds": round(seconds, 3),
            "sources": sources,
            "estimated_usd": round(base_usd + retry * dearest, 4),
        }

    line_items.append({
        "tool": video_sourcing, "operation": "visual footage", "quantity": 0,
        "estimated_usd": 0.0,
        "notes": (
            "licensed manually by a person; subscription/licence cost is "
            "externally managed and excluded from automated API spend"
            if video_sourcing == "licensed_manual"
            else "acquired by native stock tools; no metered API cost"
        ),
    })
    line_items.append({
        "tool": "audio_mixer+video_compose", "operation": "local mixing, render and QC",
        "quantity": 0, "estimated_usd": 0.0, "notes": "local FFmpeg; no metered API cost",
    })

    total = round(sum(item["estimated_usd"] for item in line_items), 4)
    if budget_cap_usd is None:
        verdict = "no_budget_set"
    elif total > budget_cap_usd:
        verdict = "over_budget"
    elif total > 0.9 * budget_cap_usd:
        verdict = "near_limit"
    else:
        verdict = "within_budget"
    cost_estimate: dict[str, Any] = {
        "total_estimated_usd": total,
        "line_items": line_items,
        "budget_verdict": verdict,
    }
    if budget_cap_usd is not None:
        cost_estimate["budget_cap_usd"] = float(budget_cap_usd)
    meta["retry_reserve_usd"] = round(sum(
        i["estimated_usd"] for i in line_items if "allowance" in i["operation"]), 4)
    meta["paid_tools"] = sorted({i["tool"] for i in line_items if i["estimated_usd"] > 0})
    return {"cost_estimate": cost_estimate, "metadata": meta}


def budget_summary(plan: Mapping[str, Any], approved_budget_usd: Optional[float] = None) -> str:
    """The block the operator reads before approving concept AND budget."""
    meta = plan["metadata"]
    est = plan["cost_estimate"]
    music = meta.get("music")
    sfx = meta.get("sfx")
    lines = [f"Target duration: {meta['target_duration_seconds'] / 60:.1f} min"]
    if music:
        strategy = music.get("generation_strategy") or {}
        if strategy.get("pricing_basis") == "fixed_per_generation":
            basis = f"fixed ${music['usd_per_request']:.2f} per generation"
        elif strategy.get("pricing_basis") == "varies_with_duration":
            basis = (f"varies with requested duration (${strategy['cost_at_min_usd']:.2f}"
                     f" at {strategy['range_seconds'][0]:g} s, "
                     f"${strategy['cost_at_max_usd']:.2f} at {strategy['range_seconds'][1]:g} s)")
        else:
            basis = f"${music['usd_per_request']:.2f} per generation"
        lines += [
            "Music generation strategy: "
            f"{music['provider']} {music['model']} - {music['seconds_per_generation']:g} s "
            "requested per paid call",
            f"Pricing basis: {basis}",
            f"Reason: {strategy.get('reason', 'stated by the caller')}",
        ]
        lines += [
            f"Music provider: {music['provider']} via {music['tool']} ({music['model']})",
            f"Music programme target: {music['unique_music_seconds'] / 60:.1f} min unique "
            f"({music['reprised_seconds'] / 60:.1f} min reprised in the edit)",
            f"Expected music generations: {music['base_requests']} + "
            f"{music['retry_requests']} retry allowance",
            f"Estimated music cost: ${music['estimated_usd']:.2f}",
        ]
    else:
        lines.append("Music provider: none (no generated music planned)")
    lines.append("")
    if sfx:
        lines += [
            f"SFX provider: {sfx['provider']} via {sfx['tool']}",
            f"Expected generated SFX source duration: {sfx['generated_source_seconds']:.0f} s",
            f"Expected SFX generations: {sfx['generations']} + "
            f"{sfx['retry_generations']} retry allowance",
            f"Estimated SFX cost: ${sfx['estimated_usd']:.2f}",
        ]
    else:
        lines.append("SFX provider: none (native/licensed audio only)")
    lines += [
        "",
        f"Estimated paid API total: ${est['total_estimated_usd']:.2f}",
        f"Retry reserve: ${meta['retry_reserve_usd']:.2f} (included in the total)",
        "Maximum approved production budget: "
        + (f"${approved_budget_usd:.2f}" if approved_budget_usd is not None
           else "<operator to approve>"),
        "Video: licensed manually - external cost, excluded from API spend",
    ]
    return "\n".join(lines)


class ApprovedBudgetTracker(CostTracker):
    """OpenMontage's `CostTracker` for one approved production, plus two checks.

    For every PAID call, in front of the ordinary estimate/reserve/execute/
    reconcile lifecycle:

    - the pipeline policy must have granted exactly this call - tool,
      operation and output path (`lib.paid_call_guard.grant`); an ad-hoc
      ``run_tool`` is refused
    - the tool may not spend beyond its OWN approved allocation (the approved
      estimate's line items for that tool), so music can never spend the SFX
      allocation or the reverse

    A refused call is recorded in ``cost_log.json`` as refunded and never
    executes. Free calls (a recovery fetch, a credit read) pass straight
    through. While a granted call executes, the paid tool's own guard
    (`lib.paid_call_guard.check_paid_call`) sees it as authorised.
    """

    #: tool name -> approved USD for that tool (set by approved_budget_tracker)
    allocations: Mapping[str, float] = {}
    #: the project this tracker's cost_log.json belongs to
    project_dir: Optional[Path] = None

    def tool_spent_usd(self, tool_name: str) -> float:
        """Spent plus still-reserved USD for one tool, across every operation."""
        return round(sum(float(e.get("actual_usd", 0.0)) + float(e.get("reserved_usd", 0.0))
                         for e in self.entries
                         if e.get("tool") == tool_name and _live(e)), 4)

    def run_tool(self, tool: Any, inputs: dict[str, Any], operation: str = "execute",
                 details: Optional[str] = None) -> Any:
        from lib import paid_call_guard as guard

        estimated = float(tool.estimate_cost(inputs))
        if estimated <= 0:
            return super().run_tool(tool, inputs, operation=operation, details=details)
        granted = guard.current_grant()
        problem, error = None, ApprovalRequiredError
        if not guard.grant_matches(granted, tool.name, operation, inputs):
            problem = ("no policy grant for this exact call - paid audio runs only through "
                       "generate_music_programme / generate_sfx_source")
        elif tool.name in self.allocations:
            spent, allowed = self.tool_spent_usd(tool.name), self.allocations[tool.name]
            if spent + estimated > allowed + 1e-9:
                problem = (f"{tool.name} would reach ${spent + estimated:.4f} of its approved "
                           f"${allowed:.4f} allocation")
                error = BudgetExceededError
        if problem:
            entry_id = self.estimate(tool.name, operation, estimated, details=details)
            self.refund(entry_id, reason=f"blocked before execution: {problem}")
            raise error(problem)
        with guard.active_call(granted):
            return super().run_tool(tool, inputs, operation=operation, details=details)


def _live(entry: Mapping[str, Any]) -> bool:
    """A cost entry that reserved or spent money (not a mere estimate or refund)."""
    return entry.get("status") in ("reserved", "completed", "failed")


def approved_budget_tracker(proposal_packet: Mapping[str, Any], project_dir: Path) -> Any:
    """An `ApprovedBudgetTracker` in CAP mode, bounded by the operator-approved budget.

    - refuses to exist without an approved proposal and ``approved_budget_usd``
    - the cap is the approved budget itself (no extra holdback: the retry
      allowance is already an explicit line inside the estimate)
    - only tools priced in the approved ``cost_estimate`` may spend; any other
      paid tool raises ``ApprovalRequiredError`` - no silent substitution
    - each paid tool is held to its own approved allocation, and spends only
      through a policy grant (see `ApprovedBudgetTracker`)
    - persists to ``<project_dir>/cost_log.json``
    """
    from lib.config_model import BudgetMode

    approval = proposal_packet.get("approval") or {}
    if approval.get("status") not in _APPROVED:
        raise BudgetNotApproved(
            f"proposal approval is {approval.get('status')!r}; paid generation needs "
            "an approved proposal"
        )
    budget = approval.get("approved_budget_usd")
    if not isinstance(budget, (int, float)) or budget < 0:
        raise BudgetNotApproved(
            "approval.approved_budget_usd is missing; the operator must approve a "
            "maximum paid budget before any paid generation"
        )
    items = (proposal_packet.get("cost_estimate") or {}).get("line_items") or []
    paid_tools = sorted({i["tool"] for i in items if i.get("estimated_usd", 0) > 0})

    tracker = ApprovedBudgetTracker(
        budget_total_usd=float(budget),
        reserve_pct=0.0,
        # The operator approved the whole budget at proposal, so the cap is the
        # gate: an over-large call raises BudgetExceededError, not a per-action
        # approval request.
        single_action_approval_usd=math.inf,
        require_approval_for_new_paid_tool=True,
        mode=BudgetMode.CAP,
        cost_log_path=Path(project_dir) / "cost_log.json",
    )
    # A resumed log must not keep an older budget or an older approval list.
    tracker.budget_total_usd = float(budget)
    tracker._approved_tools = set()
    for name in paid_tools:
        tracker.approve_tool(name)
    tracker.allocations = {
        name: round(sum(float(i.get("estimated_usd", 0)) for i in items if i["tool"] == name), 4)
        for name in paid_tools
    }
    tracker.project_dir = Path(project_dir).resolve()
    tracker._save()
    return tracker


def _require_project_tracker(tracker: Any, project_dir: Path) -> None:
    if not isinstance(tracker, ApprovedBudgetTracker) or \
            tracker.project_dir != Path(project_dir).resolve():
        raise BudgetNotApproved(
            "paid audio needs approved_budget_tracker(proposal_packet, project_dir) for this "
            "same project; a plain CostTracker or another project's tracker is refused")


class PaidAudioInProgress(RuntimeError):
    """Another session is running paid audio generation for this project."""


_PAID_AUDIO_LOCK = Path("work") / "paid_audio.lock"


@contextlib.contextmanager
def _paid_audio_lock(project_dir: Path):
    """An OS lock held for the whole paid-audio run; a crash releases it."""
    path = Path(project_dir) / _PAID_AUDIO_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise PaidAudioInProgress(
            f"paid audio is already running for {Path(project_dir).name} in another session")
    try:
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def paid_audio_in_progress(project_dir: Path) -> bool:
    """True while a paid-audio run holds this project's lock.

    While it does, a reserved cost entry, a ``submitting`` ledger row or a
    ``submitted`` task record is the expected in-flight state of that run, not
    a contradiction. Only once the lock is free do they mean an interruption.
    """
    try:
        with _paid_audio_lock(project_dir):
            return False
    except PaidAudioInProgress:
        return True


# --------------------------------------------------------------------------
# Generated music: cost-efficient request length, and honest accounting
# --------------------------------------------------------------------------
#
# Generic over the tool contract: the duration range comes from the tool's own
# `input_schema`, the model check from its `dry_run`, and the price from its
# `estimate_cost`. No provider or model is named here.

_EFFICIENT_REASON = "maximum verified duration costs the same as minimum verified duration"


def generation_duration_strategy(tool: Any, tool_inputs: Mapping[str, Any]) -> dict[str, Any]:
    """How request length affects cost for this tool and these inputs.

    Reads ``input_schema.properties.duration_seconds.{minimum,maximum}``,
    confirms the selected model accepts both ends (the tool's own
    ``dry_run``), and prices both ends with ``estimate_cost``. When the two
    prices are equal, the maximum is the cost-efficient request length.
    Raises ValueError when the tool or model offers no explicit duration.
    """
    prop = ((getattr(tool, "input_schema", None) or {}).get("properties") or {}).get(
        "duration_seconds") or {}
    low, high = prop.get("minimum"), prop.get("maximum")
    if low is None or high is None:
        raise ValueError(f"{tool.name} declares no bounded duration_seconds")
    at_low = {**dict(tool_inputs), "duration_seconds": float(low)}
    at_high = {**dict(tool_inputs), "duration_seconds": float(high)}
    cost_low, cost_high = _unit_cost(tool, at_low), _unit_cost(tool, at_high)
    for probe in (at_low, at_high):
        blocker = (tool.dry_run(dict(probe)) or {}).get("blocker")
        if blocker:
            raise ValueError(
                f"{tool.name} cannot request {probe['duration_seconds']:g} s with these "
                f"inputs: {blocker}"
            )
    fixed = abs(cost_high - cost_low) < 1e-9
    return {
        "range_seconds": [float(low), float(high)],
        "cost_at_min_usd": cost_low,
        "cost_at_max_usd": cost_high,
        "pricing_basis": "fixed_per_generation" if fixed else "varies_with_duration",
        "cost_efficient_seconds": float(high) if fixed else None,
    }


def _duration_choice(tool: Any, base_inputs: Mapping[str, Any],
                     music: Mapping[str, Any]) -> dict[str, Any]:
    explicit = music.get("seconds_per_generation")
    reason = (music.get("generation_duration_reason") or "").strip()
    try:
        strategy = generation_duration_strategy(tool, base_inputs)
    except ValueError as exc:
        if explicit is None:
            raise ValueError(
                f"{exc}; state music.seconds_per_generation explicitly"
            ) from exc
        return {"selected_seconds": float(explicit), "source": "caller",
                "pricing_basis": "unknown", "reason": reason or str(exc)}

    note = ("requested length is a generation target; only the measured length "
            "of accepted candidates counts toward the programme")
    if explicit is None:
        if strategy["pricing_basis"] != "fixed_per_generation":
            raise ValueError(
                f"{tool.name} cost rises with requested duration "
                f"(${strategy['cost_at_min_usd']:.4f} -> ${strategy['cost_at_max_usd']:.4f}); "
                "the longest request is not automatically cheapest - state "
                "music.seconds_per_generation and generation_duration_reason"
            )
        return {**strategy, "selected_seconds": strategy["cost_efficient_seconds"],
                "source": "derived_from_tool_contract", "reason": _EFFICIENT_REASON,
                "note": note}

    selected = float(explicit)
    low, high = strategy["range_seconds"]
    if not low <= selected <= high:
        raise ValueError(f"seconds_per_generation must be within [{low:g}, {high:g}]")
    efficient = strategy["cost_efficient_seconds"]
    if efficient is not None and selected < efficient and not reason:
        raise ValueError(
            f"{selected:g} s is shorter than the cost-efficient {efficient:g} s at the "
            "same price per call; record a concrete creative or provider reason in "
            "music.generation_duration_reason"
        )
    return {**strategy, "selected_seconds": selected, "source": "caller",
            "reason": reason or ("cost-efficient maximum" if selected == efficient
                                 else "caller choice on duration-dependent pricing"),
            "note": note}


def account_music_candidates(
    candidates: list[Mapping[str, Any]],
    decisions: Mapping[int, Mapping[str, Any]],
    probe: Optional[Any] = None,
) -> dict[str, Any]:
    """Accepted seconds contributed by ONE paid generation's candidates.

    ``decisions`` maps each candidate ``index`` to a decision - every candidate
    needs one; nothing is accepted by default. A decision is either the full
    form from `decide_music_candidate` (``{"outcome": "accepted" | "rejected" |
    "uncertain", ...}``) or the older ``{"accepted": bool, "reason": str}``.
    An accepted candidate counts at its MEASURED length (ffprobe), never the
    requested or provider-reported length; a rejected, uncertain or missing
    one counts zero. An accepted candidate that cannot be measured raises.
    """
    if probe is None:
        from tools.analysis.audio_probe import probe_duration as probe
    rows: list[dict[str, Any]] = []
    total = 0.0
    for cand in candidates:
        index = cand.get("index")
        if index not in decisions:
            raise ValueError(f"candidate {index} has no accept/reject decision")
        decision = decisions[index]
        outcome = decision.get("outcome") or ("accepted" if decision.get("accepted") else "rejected")
        accepted = outcome == "accepted" and bool(cand.get("downloaded"))
        measured = cand.get("measured_seconds")
        if measured is None and cand.get("path"):
            measured = probe(cand["path"])
        if accepted and not measured:
            raise ValueError(f"accepted candidate {index} could not be measured")
        seconds = float(measured) if accepted else 0.0
        total += seconds
        row = {
            "index": index,
            "path": cand.get("path"),
            "provider_reported_seconds": cand.get("duration_seconds"),
            "measured_seconds": measured,
            "outcome": outcome if cand.get("downloaded") or outcome != "accepted" else "uncertain",
            "accepted": accepted,
            "accepted_seconds": seconds,
            "reason": decision.get("reason") or "",
        }
        for key in ("criterion", "evidence", "screen"):
            if key in decision:
                row[key] = decision[key]
        rows.append(row)
    return {"accepted_seconds": round(total, 3), "candidates": rows}


def next_music_request(
    *,
    target_seconds: float,
    accepted_seconds: float,
    tracker: Any,
    tool: Any,
    inputs: Mapping[str, Any],
    requests_made: int,
    max_requests: int,
) -> dict[str, Any]:
    """The arithmetic of whether one more generation would help.

    This is arithmetic only and authorises nothing: its inputs are whatever
    the caller passes. Paid calls are authorised by `authorize_music_request`,
    which rebuilds progress from the project's durable records first.
    The planned request count and retry allowance are a ceiling, never a
    quota: once accepted music meets the target, the answer is no.
    """
    remaining = max(0.0, float(target_seconds) - float(accepted_seconds))
    state = {
        "target_seconds": float(target_seconds),
        "accepted_seconds": float(accepted_seconds),
        "remaining_seconds": round(remaining, 3),
        "requests_made": requests_made,
        "max_requests": max_requests,
        "spent_usd": round(tracker.budget_spent_usd, 4),
        "usable_budget_usd": round(tracker.usable_budget_usd, 4),
    }
    if remaining <= 0:
        return {**state, "generate": False, "reason": "target_met"}
    if requests_made >= max_requests:
        return {**state, "generate": False, "reason": "request_ceiling_reached"}
    cost = _unit_cost(tool, inputs)
    state["next_call_usd"] = cost
    if cost > tracker.usable_budget_usd:
        return {**state, "generate": False, "reason": "budget_would_be_exceeded"}
    return {**state, "generate": True, "reason": "more_accepted_music_needed"}


# --------------------------------------------------------------------------
# Paid music: evidence-based candidate screening
# --------------------------------------------------------------------------
#
# Three outcomes, never two. A rejection must cite what failed and the
# evidence - a prohibited term found as a whole word in a named field, or a
# measured value past a stated limit - and that evidence is re-checked here.
# Anything the code cannot substantiate is `uncertain`, and an uncertain
# candidate stops paid generation until the operator reviews it. The
# prohibited terms are inputs (a channel's BRAND.md); none are named here.

ACCEPTED, REJECTED, UNCERTAIN = "accepted", "rejected", "uncertain"
MUSIC_OUTCOMES = (ACCEPTED, REJECTED, UNCERTAIN)

_TOKEN = re.compile(r"[a-z0-9]+")
#: Words that negate a term immediately after them ("no <term>", "without
#: <term>", "free of <term>", "non-<term>").
_NEGATORS = frozenset({"no", "not", "without", "non", "zero", "free", "never", "nor",
                       "avoid", "avoiding", "exclude", "excluding", "excludes"})
#: ...and after it ("<term>-free").
_POST_NEGATORS = frozenset({"free", "none", "excluded", "removed"})
_NEGATION_WINDOW = 2
_CLAUSE_BREAK = re.compile(r"[,;:.!?()\[\]{}|/\n]|\b(?:but|and|with|plus|then)\b")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text.lower())]


def find_prohibited_terms(text: Optional[str], terms: Any) -> list[dict[str, Any]]:
    """Whole-word occurrences of ``terms`` in ``text``.

    A term matches only as complete words (spacing and hyphenation are free;
    the last word may take a plural ``s``/``es``), so ``sing`` never matches
    inside ``phrasing`` and ``voice`` never inside ``invoice``. Each match says
    whether it is negated ("no <term>", "<term>-free") within its own clause.
    """
    text = text or ""
    tokens = _tokens(text)
    words = [t[0] for t in tokens]
    lowered = text.lower()
    found: list[dict[str, Any]] = []
    for term in terms:
        parts = [t[0] for t in _tokens(str(term))]
        if not parts:
            continue
        n = len(parts)
        for i in range(len(words) - n + 1):
            head_ok = words[i:i + n - 1] == parts[:-1]
            last = words[i + n - 1]
            if not head_ok or last not in (parts[-1], parts[-1] + "s", parts[-1] + "es"):
                continue
            start, end = tokens[i][1], tokens[i + n - 1][2]
            found.append({
                "term": str(term),
                "matched_text": text[start:end],
                "span": [start, end],
                "negated": _is_negated(lowered, tokens, i, i + n - 1),
            })
    return found


def _is_negated(lowered: str, tokens: list[tuple[str, int, int]], first: int, last: int) -> bool:
    start, end = tokens[first][1], tokens[last][2]
    clause_start = max((m.end() for m in _CLAUSE_BREAK.finditer(lowered, 0, start)), default=0)
    before = [w for w, s, _ in tokens[max(0, first - _NEGATION_WINDOW):first] if s >= clause_start]
    if any(w in _NEGATORS for w in before):
        return True
    brk = _CLAUSE_BREAK.search(lowered, end)
    clause_end = brk.start() if brk else len(lowered)
    after = [w for w, s, _ in tokens[last + 1:last + 1 + _NEGATION_WINDOW] if s < clause_end]
    return any(w in _POST_NEGATORS for w in after)


def _field_text(candidate: Mapping[str, Any], field: str) -> str:
    value = candidate.get(field)
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return "" if value is None else str(value)


def screen_music_candidate(
    candidate: Mapping[str, Any],
    *,
    prohibited_terms: Any,
    fields: Any = ("title", "tags"),
    compliance_terms: Any = (),
    measured_seconds: Optional[float] = None,
    measurements: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> dict[str, Any]:
    """The bounded, evidence-based screen for ONE candidate.

    Returns ``outcome``: ``rejected`` (with ``failures`` citing each failed
    criterion and its evidence), ``uncertain`` (with ``uncertainties``) or
    ``passed``. ``passed`` is not acceptance: the creative evaluation still
    decides (`decide_music_candidate`).

    - ``prohibited_terms`` are matched as whole words in each of ``fields``;
      a negated mention ("no vocals") is recorded but is not a failure.
    - ``compliance_terms`` are metadata claims of compliance ("instrumental").
      A prohibited term alongside one is contradictory metadata: uncertain.
    - ``measured_seconds`` is the ffprobe length; without it the candidate is
      uncertain (a failed probe proves nothing either way).
    - ``thresholds`` maps a metric to ``{"max": x}`` and/or ``{"min": y}``;
      a metric missing from ``measurements`` is uncertain, not a pass.
    """
    failures: list[dict[str, Any]] = []
    uncertainties: list[dict[str, Any]] = []
    negated: list[dict[str, Any]] = []
    compliance: list[dict[str, Any]] = []
    for field in fields:
        text = _field_text(candidate, field)
        for hit in find_prohibited_terms(text, prohibited_terms):
            row = {"criterion": "prohibited_term", "field": field, **hit}
            (negated if hit["negated"] else failures).append(row)
        for hit in find_prohibited_terms(text, compliance_terms):
            if not hit["negated"]:
                compliance.append({"field": field, **hit})
    if failures and compliance:
        uncertainties.append({
            "criterion": "contradictory_metadata",
            "evidence": {"prohibited": failures, "compliance": compliance},
        })
        failures = []

    if not measured_seconds:
        uncertainties.append({"criterion": "duration_unmeasured",
                              "evidence": {"path": candidate.get("path"),
                                           "measured_seconds": measured_seconds}})
    for metric, limit in (thresholds or {}).items():
        value = (measurements or {}).get(metric)
        if value is None:
            uncertainties.append({"criterion": "measurement_missing",
                                  "evidence": {"metric": metric}})
            continue
        for comparator in ("max", "min"):
            bound = limit.get(comparator)
            if bound is None:
                continue
            if (comparator == "max" and value > bound) or (comparator == "min" and value < bound):
                failures.append({"criterion": "measurement", "metric": metric,
                                 "value": value, "limit": bound, "comparator": comparator})

    if uncertainties and not failures:
        outcome = UNCERTAIN
    elif failures:
        outcome = REJECTED
    else:
        outcome = "passed"
    return {
        "outcome": outcome,
        "failures": failures,
        "uncertainties": uncertainties,
        "negated_mentions": negated,
        "compliance_mentions": compliance,
        "fields_checked": list(fields),
        "terms_checked": [str(t) for t in prohibited_terms],
        "measured_seconds": measured_seconds,
        "measurements": dict(measurements or {}),
        "limitation": "metadata and measurement screen only; no vocal detector was run",
    }


def _verify_rejection(candidate: Mapping[str, Any], screen: Mapping[str, Any],
                      evaluation: Mapping[str, Any],
                      prohibited_terms: Any) -> Optional[str]:
    """None when the evaluator's rejection is substantiated, else why not."""
    criterion = evaluation.get("criterion")
    evidence = evaluation.get("evidence")
    if not criterion or not evidence:
        return "rejection states no criterion and evidence"
    if criterion == "prohibited_term":
        if not isinstance(evidence, Mapping) or not evidence.get("field") or not evidence.get("term"):
            return "a prohibited_term rejection must name the field and the term"
        if str(evidence["term"]).lower() not in {str(t).lower() for t in prohibited_terms}:
            return f"{evidence['term']!r} is not on the prohibited list"
        hits = find_prohibited_terms(_field_text(candidate, evidence["field"]), [evidence["term"]])
        if not any(not h["negated"] for h in hits):
            return (f"{evidence['term']!r} does not occur as a whole, un-negated word in "
                    f"{evidence['field']!r}")
        return None
    if criterion == "measurement":
        if not isinstance(evidence, Mapping):
            return "a measurement rejection must give metric, value, limit and comparator"
        metric, value = evidence.get("metric"), evidence.get("value")
        limit, comparator = evidence.get("limit"), evidence.get("comparator")
        if metric is None or value is None or limit is None or comparator not in ("max", "min"):
            return "a measurement rejection must give metric, value, limit and comparator"
        measured = (screen.get("measurements") or {}).get(metric)
        if measured is not None and abs(float(measured) - float(value)) > 0.05:
            return f"{metric} was measured at {measured}, not {value}"
        if (comparator == "max" and not value > limit) or (comparator == "min" and not value < limit):
            return f"{metric}={value} does not fail its {comparator} limit {limit}"
        return None
    if criterion == "creative":
        return None  # a stated creative judgement with its observation
    return f"unknown rejection criterion {criterion!r}"


def decide_music_candidate(
    candidate: Mapping[str, Any],
    screen: Mapping[str, Any],
    evaluation: Optional[Mapping[str, Any]],
    *,
    prohibited_terms: Any,
) -> dict[str, Any]:
    """Combine the bounded screen with the caller's technical/creative evaluation.

    ``evaluation`` is ``{"outcome", "criterion", "evidence", "reason"}`` (or
    the older ``{"accepted", "reason"}``). Accepted only when the screen passed
    AND the evaluation accepts. A rejection counts only when the screen or the
    evaluation substantiates it; an unsubstantiated or contradictory result is
    uncertain.
    """
    base = {"index": candidate.get("index"), "screen": dict(screen)}
    if evaluation is None and screen["outcome"] == REJECTED:
        return {**base, "outcome": REJECTED, "criterion": screen["failures"][0]["criterion"],
                "evidence": screen["failures"], "reason": "failed the screen"}
    if evaluation is None:
        return {**base, "outcome": UNCERTAIN, "criterion": "no_evaluation",
                "reason": "the candidate was not evaluated"}
    claimed = evaluation.get("outcome") or (ACCEPTED if evaluation.get("accepted") else REJECTED)
    if claimed not in MUSIC_OUTCOMES:
        return {**base, "outcome": UNCERTAIN, "criterion": "invalid_evaluation",
                "reason": f"evaluation outcome {claimed!r} is not one of {MUSIC_OUTCOMES}"}
    reason = evaluation.get("reason") or ""

    if screen["outcome"] == REJECTED:
        if claimed == ACCEPTED:
            return {**base, "outcome": UNCERTAIN, "criterion": "contradictory_result",
                    "evidence": screen["failures"],
                    "reason": "the evaluation accepts a candidate the screen rejects"}
        return {**base, "outcome": REJECTED, "criterion": screen["failures"][0]["criterion"],
                "evidence": screen["failures"], "reason": reason or "failed the screen"}
    if screen["outcome"] == UNCERTAIN:
        return {**base, "outcome": UNCERTAIN, "criterion": screen["uncertainties"][0]["criterion"],
                "evidence": screen["uncertainties"], "reason": reason}
    if claimed == ACCEPTED:
        return {**base, "outcome": ACCEPTED, "criterion": evaluation.get("criterion") or "passed",
                "evidence": evaluation.get("evidence"), "reason": reason}
    if claimed == UNCERTAIN:
        return {**base, "outcome": UNCERTAIN, "criterion": evaluation.get("criterion") or "evaluation_uncertain",
                "evidence": evaluation.get("evidence"), "reason": reason}
    problem = _verify_rejection(candidate, screen, evaluation, prohibited_terms)
    if problem:
        return {**base, "outcome": UNCERTAIN, "criterion": "unsubstantiated_rejection",
                "evidence": {"claimed": dict(evaluation), "problem": problem},
                "reason": f"rejection not substantiated: {problem}"}
    return {**base, "outcome": REJECTED, "criterion": evaluation["criterion"],
            "evidence": evaluation["evidence"], "reason": reason}


# --------------------------------------------------------------------------
# Paid music: approved limits, durable progress, authorisation, recovery
# --------------------------------------------------------------------------
#
# Progress is never taken from the caller. It is rebuilt, before every paid
# request, from four durable records that must agree: `cost_log.json` (every
# reserved or executed call), the music ledger (`work/paid_music_ledger.json`,
# written before and after every call), the files on disk with the tool's
# pending-task records beside them, and the asset manifest when one exists.

MUSIC_LEDGER = Path("work") / "paid_music_ledger.json"
_MUSIC_OPERATION = re.compile(r"^music generation (\d+)$")
#: What follows the programme's base stem in any file one request wrote -
#: a candidate, a tool's task record, a partial download.
_REQUEST_FILE = re.compile(r"^_g(\d{2,})(?:__cand\d+)?(?:\.|$)")
_MEASURE_TOLERANCE_S = 0.5


class MusicLimitsUnavailable(BudgetNotApproved):
    """The approved proposal does not state a usable music plan."""


def approved_music_limits(proposal_packet: Mapping[str, Any], tool_name: str) -> dict[str, Any]:
    """Target, request ceiling and USD allocation for ``tool_name`` - from the approval.

    - target: ``metadata.paid_audio_plan.music.unique_music_seconds``
    - ceiling: the approved ``cost_estimate`` line quantities for the tool
      (base generations + retry allowance), cross-checked against the plan
    - allocation: the approved line items' USD for this tool only, so music
      can never spend what the estimate set aside for another tool (SFX)
    """
    approval = proposal_packet.get("approval") or {}
    if approval.get("status") not in _APPROVED:
        raise BudgetNotApproved(f"proposal approval is {approval.get('status')!r}")
    plan = ((proposal_packet.get("metadata") or {}).get("paid_audio_plan") or {}).get("music")
    if not plan or plan.get("tool") != tool_name:
        raise MusicLimitsUnavailable(
            f"the approved proposal has no paid_audio_plan.music for {tool_name!r}")
    items = [i for i in ((proposal_packet.get("cost_estimate") or {}).get("line_items") or [])
             if i.get("tool") == tool_name]
    base = sum(int(i.get("quantity", 0)) for i in items if i.get("operation") == "music generation")
    retry = sum(int(i.get("quantity", 0)) for i in items
                if i.get("operation") == "music retry/rejection allowance")
    if base < 1:
        raise MusicLimitsUnavailable(f"the approved estimate has no music generation line for {tool_name!r}")
    if (int(plan.get("base_requests", base)), int(plan.get("retry_requests", retry))) != (base, retry):
        raise MusicLimitsUnavailable(
            f"approved plan says {plan.get('base_requests')}+{plan.get('retry_requests')} requests "
            f"but the approved estimate says {base}+{retry}; the proposal contradicts itself")
    return {
        "tool": tool_name,
        "target_seconds": float(plan["unique_music_seconds"]),
        "seconds_per_generation": float(plan["seconds_per_generation"]),
        "base_requests": base,
        "retry_requests": retry,
        "max_requests": base + retry,
        "allocation_usd": round(sum(float(i.get("estimated_usd", 0)) for i in items), 4),
        "usd_per_request": float(plan["usd_per_request"]),
        "approved_budget_usd": float(approval.get("approved_budget_usd") or 0.0),
    }


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def load_music_ledger(project_dir: Path) -> dict[str, Any]:
    path = Path(project_dir) / MUSIC_LEDGER
    if not path.exists():
        return {"version": 1, "requests": [], "reviews": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_music_ledger(project_dir: Path, ledger: Mapping[str, Any]) -> None:
    _write_json_atomic(Path(project_dir) / MUSIC_LEDGER, ledger)


def _request_path(base: Path, n: int) -> Path:
    return base.with_name(f"{base.stem}_g{n:02d}{base.suffix}")


def _safe_probe(probe: Any, path: Optional[str]) -> Optional[float]:
    if not path or not Path(path).exists():
        return None
    try:
        value = probe(path)
    except Exception:
        return None
    return float(value) if value else None


def _music_cost_entries(entries: list[Mapping[str, Any]], tool_name: str) -> dict[int, list]:
    by_request: dict[int, list] = {}
    for entry in entries:
        match = _MUSIC_OPERATION.match(str(entry.get("operation", "")))
        if entry.get("tool") == tool_name and match:
            by_request.setdefault(int(match.group(1)), []).append(entry)
    return by_request


def _reviews_for(ledger: Mapping[str, Any], n: int) -> list[Mapping[str, Any]]:
    return [r for r in ledger.get("reviews", []) if r.get("request") == n]


def _resolved_candidates(request: Mapping[str, Any],
                         reviews: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The request's candidate decisions with operator resolutions applied."""
    resolutions: dict[int, Mapping[str, Any]] = {}
    for review in reviews:
        for key, value in (review.get("resolutions") or {}).items():
            resolutions[int(key)] = value
    rows = []
    for cand in request.get("candidates") or []:
        row = dict(cand)
        if cand.get("index") in resolutions:
            res = resolutions[cand["index"]]
            row.update(outcome=res["outcome"], criterion="operator_review",
                       reason=res.get("reason", ""), reviewed=True)
        rows.append(row)
    return rows


def _asset_manifest(project_dir: Path) -> Optional[Mapping[str, Any]]:
    path = Path(project_dir) / "checkpoint_assets.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("artifacts") or {}).get("asset_manifest")


def reconcile_music_progress(
    *,
    project_dir: Path,
    tool: Any,
    cost_entries: list[Mapping[str, Any]],
    output_base: Optional[Path] = None,
    probe: Optional[Any] = None,
    asset_manifest: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Paid-music progress rebuilt from durable records - never from the caller.

    Returns ``requests_made`` (every call that was reserved or executed),
    ``accepted_seconds`` (accepted candidates, re-measured on disk),
    ``spent_usd`` for this tool, ``recoverable`` (billed tasks with a
    ``task_id`` the free ``fetch`` can recover), ``issues`` (records that
    disagree, or a charge whose outcome is unknown) and ``review`` (why the
    next paid call needs the operator first, if it does).
    """
    if probe is None:
        from tools.analysis.audio_probe import probe_duration as probe
    project_dir = Path(project_dir)
    ledger = load_music_ledger(project_dir)
    if output_base is None and ledger.get("output_base"):
        output_base = Path(ledger["output_base"])
    requests = {int(r["request"]): r for r in ledger.get("requests", [])}
    costs = _music_cost_entries(cost_entries, tool.name)
    # Every live paid call of the music tool must be a programme request.
    outside = [{"issue": "paid_call_outside_programme", "operation": e.get("operation"),
                "entry": e.get("id")}
               for e in cost_entries
               if e.get("tool") == tool.name and _live(e)
               and float(e.get("estimated_usd", 0.0)) > 0
               and not _MUSIC_OPERATION.match(str(e.get("operation", "")))]
    read_record = getattr(tool, "pending_task_record", None)
    issues: list[dict[str, Any]] = list(outside)
    recoverable: list[dict[str, Any]] = []
    executed: list[int] = []
    accepted_seconds = 0.0
    accepted_paths: set[str] = set()
    not_accepted_paths: set[str] = set()

    known = set(requests) | set(costs)
    if output_base is not None and output_base.parent.exists():
        for path in output_base.parent.glob(f"{output_base.stem}_g*"):
            match = _REQUEST_FILE.match(path.name[len(output_base.stem):])
            if match and int(match.group(1)) not in known:
                issues.append({"issue": "unrecorded_paid_audio_file", "path": str(path),
                               "detail": "a generated file exists that no cost entry or "
                                         "ledger request accounts for"})

    for n in sorted(known):
        req = requests.get(n)
        entries = [e for e in costs.get(n, []) if _live(e)]
        charged = _reviews_charge(ledger, n)
        out = Path(req["output_path"]) if req and req.get("output_path") else (
            _request_path(output_base, n) if output_base is not None else None)
        record = read_record(out) if (read_record and out is not None) else None
        task_id = (req or {}).get("task_id") or (record or {}).get("task_id") or charged.get("task_id")

        if len(entries) > 1:
            issues.append({"issue": "duplicate_cost_entries", "request": n,
                           "entries": [e.get("id") for e in entries]})
        if not entries:
            if req and req.get("status") not in ("submitting", "not_executed"):
                issues.append({"issue": "ledger_request_without_cost_entry", "request": n})
            elif record and record.get("status") != "submitting":
                issues.append({"issue": "provider_task_without_cost_entry", "request": n,
                               "task_id": task_id})
            continue
        executed.append(n)
        entry = entries[-1]
        status = (req or {}).get("status")

        if status == "completed":
            cost = float((req or {}).get("cost_usd") or 0.0)
            if entry.get("status") == "reserved" or abs(float(entry.get("actual_usd", 0.0)) - cost) > 1e-6:
                issues.append({"issue": "ledger_cost_disagrees_with_cost_log", "request": n,
                               "ledger_usd": cost, "cost_log": {k: entry.get(k) for k in
                                                                ("status", "actual_usd")}})
            for cand in _resolved_candidates(req, _reviews_for(ledger, n)):
                if cand.get("outcome") != ACCEPTED:
                    if cand.get("path"):
                        not_accepted_paths.add(str(Path(cand["path"])))
                    continue
                measured = _safe_probe(probe, cand.get("path"))
                recorded = cand.get("measured_seconds")
                if measured is None:
                    issues.append({"issue": "accepted_file_missing_or_unmeasurable", "request": n,
                                   "path": cand.get("path")})
                    continue
                if recorded is not None and abs(measured - float(recorded)) > _MEASURE_TOLERANCE_S:
                    issues.append({"issue": "accepted_file_changed", "request": n,
                                   "path": cand.get("path"), "recorded_seconds": recorded,
                                   "measured_seconds": measured})
                    continue
                accepted_seconds += measured
                accepted_paths.add(str(Path(cand["path"])))
            continue

        interrupted = entry.get("status") == "reserved" or status in (None, "submitting")
        billed_unfetched = status == "failed" and (req or {}).get("charge_status") == "charged" \
            and not (req or {}).get("candidates")
        if (interrupted or billed_unfetched) and task_id and not charged.get("abandon_recovery"):
            recoverable.append({"request": n, "task_id": task_id, "output_path": str(out),
                                "cost_entry_id": entry.get("id"),
                                "cost_entry_status": entry.get("status"),
                                "reason": "interrupted" if interrupted else "billed_not_downloaded"})
        elif interrupted:
            if charged.get("charge_outcome"):
                continue  # the operator established the outcome; nothing to recover
            if entry.get("status") == "reserved":
                issues.append({"issue": "charge_outcome_unknown", "request": n,
                               "cost_entry": {k: entry.get(k) for k in ("id", "status")},
                               "detail": "a paid call was started but no task_id was recorded; "
                                         "check the provider's logs and record the outcome with "
                                         "record_music_review(..., charge_outcome=...)"})
            else:
                issues.append({"issue": "paid_request_not_in_ledger", "request": n,
                               "cost_entry": {k: entry.get(k) for k in ("id", "status", "actual_usd")},
                               "detail": "cost_log records this paid call but the music ledger "
                                         "holds no result or task_id for it, so its candidates "
                                         "cannot be counted; the operator must review it"})
        elif status == "failed" and (req or {}).get("charge_status") in (None, "unknown") \
                and not charged.get("charge_outcome"):
            issues.append({"issue": "charge_outcome_unknown", "request": n,
                           "detail": (req or {}).get("error") or "provider failure"})

    manifest = asset_manifest if asset_manifest is not None else _asset_manifest(project_dir)
    for asset in (manifest or {}).get("assets") or []:
        if asset.get("source_tool") != tool.name or asset.get("type") != "music":
            continue
        path = str(Path(asset.get("path", "")))
        if path in accepted_paths:
            continue
        issues.append({"issue": "manifest_music_not_accepted_in_ledger", "asset": asset.get("id"),
                       "path": asset.get("path"),
                       "detail": "rejected or uncertain in the ledger" if path in not_accepted_paths
                       else "no ledger record accepts this file"})

    spent = sum(float(e.get("actual_usd", 0.0)) + float(e.get("reserved_usd", 0.0))
                for e in cost_entries if e.get("tool") == tool.name and _live(e))
    return {
        "requests_made": len(executed),
        "last_request": max(executed, default=0),
        "next_request": max(known, default=0) + 1,
        "accepted_seconds": round(accepted_seconds, 3),
        "spent_usd": round(spent, 4),
        "recoverable": recoverable,
        "issues": issues,
        "review": _pending_review(ledger, requests, max(executed, default=0)),
        "ledger": ledger,
    }


def _reviews_charge(ledger: Mapping[str, Any], n: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for review in _reviews_for(ledger, n):
        for key in ("charge_outcome", "task_id", "abandon_recovery"):
            if review.get(key):
                out[key] = review[key]
    return out


def _pending_review(ledger: Mapping[str, Any], requests: Mapping[int, Mapping[str, Any]],
                    last: int) -> Optional[dict[str, Any]]:
    """Why the last completed request needs the operator before another call."""
    req = requests.get(last)
    if not req:
        return None
    reviews = _reviews_for(ledger, last)
    authorised = any(r.get("authorize_next_request") for r in reviews)
    if req.get("status") != "completed":
        return {"request": last, "reasons": [], "authorised": authorised, "blocking": False}
    cands = _resolved_candidates(req, reviews)
    reasons = []
    if any(c.get("outcome") == UNCERTAIN for c in cands):
        reasons.append("uncertain_candidates")
    if cands and all(c.get("outcome") == REJECTED for c in cands):
        reasons.append("all_candidates_rejected")
    if req.get("charge_verification") == "unverified":
        reasons.append("charge_unverified")
    return {"request": last, "reasons": reasons, "authorised": authorised,
            "blocking": bool(reasons) and not authorised}


def record_music_review(
    project_dir: Path,
    *,
    request: int,
    reviewer: str,
    note: str,
    resolutions: Optional[Mapping[int, Mapping[str, Any]]] = None,
    authorize_next_request: bool = False,
    charge_outcome: Optional[str] = None,
    task_id: Optional[str] = None,
    abandon_recovery: bool = False,
) -> dict[str, Any]:
    """Record an explicit operator decision about paid request ``request``.

    Call this only with what the operator actually decided in chat.
    ``resolutions`` settle uncertain candidates (``{index: {"outcome":
    "accepted" | "rejected", "reason"}}``); ``authorize_next_request`` permits
    ONE further paid request after it; ``charge_outcome`` (``charged`` /
    ``not_charged``) and ``task_id`` settle a call whose charge was unknown;
    ``abandon_recovery`` stops retrying a free recovery that keeps failing
    (the request stays counted and charged, with nothing accepted from it).
    """
    if abandon_recovery and charge_outcome is None:
        raise ValueError("abandoning a recovery must state the charge_outcome")
    for index, res in (resolutions or {}).items():
        if res.get("outcome") not in (ACCEPTED, REJECTED) or not res.get("reason"):
            raise ValueError(f"resolution for candidate {index} needs outcome accepted|rejected "
                             "and the operator's reason")
    if charge_outcome not in (None, "charged", "not_charged"):
        raise ValueError("charge_outcome must be 'charged' or 'not_charged'")
    if not reviewer or not note:
        raise ValueError("a review records who decided and what they said")
    ledger = load_music_ledger(project_dir)
    review = {
        "request": int(request), "reviewer": reviewer, "note": note,
        "resolutions": {str(k): dict(v) for k, v in (resolutions or {}).items()},
        "authorize_next_request": bool(authorize_next_request),
        "charge_outcome": charge_outcome, "task_id": task_id,
        "abandon_recovery": bool(abandon_recovery),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    ledger.setdefault("reviews", []).append(review)
    _save_music_ledger(project_dir, ledger)
    return review


def authorize_music_request(
    *,
    state: Mapping[str, Any],
    limits: Mapping[str, Any],
    tracker: Any,
    tool: Any,
    inputs: Mapping[str, Any],
    max_requests: Optional[int] = None,
    claimed_requests_made: Optional[int] = None,
    claimed_accepted_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Whether ONE more paid music request is permitted, and every fact behind it.

    Each fact is established independently: the approved target and ceiling
    (`approved_music_limits`), measured accepted seconds and previous
    requests (`reconcile_music_progress`), actual spend against this tool's
    own approved allocation and the tracker's remaining cap, and the need.
    A caller's claim may only be confirmed by the records, never override
    them; ``max_requests`` may lower the approved ceiling, never raise it.
    """
    ceiling = limits["max_requests"] if max_requests is None else min(int(max_requests),
                                                                      limits["max_requests"])
    remaining = max(0.0, limits["target_seconds"] - state["accepted_seconds"])
    spent = state["spent_usd"]
    checks: dict[str, Any] = {
        "target_seconds": limits["target_seconds"],
        "accepted_seconds": state["accepted_seconds"],
        "remaining_seconds": round(remaining, 3),
        "requests_made": state["requests_made"],
        "max_requests": ceiling,
        "base_requests": limits["base_requests"],
        "tool_allocation_usd": limits["allocation_usd"],
        "tool_spent_usd": spent,
        "tool_allocation_remaining_usd": round(limits["allocation_usd"] - spent, 4),
        "budget_spent_usd": round(tracker.budget_spent_usd, 4),
        "usable_budget_usd": round(tracker.usable_budget_usd, 4),
    }

    def stop(reason: str, **detail: Any) -> dict[str, Any]:
        return {"generate": False, "reason": reason, "checks": checks, **detail}

    contradictions = {}
    if claimed_requests_made is not None and int(claimed_requests_made) != state["requests_made"]:
        contradictions["requests_made"] = {"claimed": claimed_requests_made,
                                           "recorded": state["requests_made"]}
    if claimed_accepted_seconds is not None and \
            abs(float(claimed_accepted_seconds) - state["accepted_seconds"]) > _MEASURE_TOLERANCE_S:
        contradictions["accepted_seconds"] = {"claimed": claimed_accepted_seconds,
                                              "recorded": state["accepted_seconds"]}
    if contradictions:
        return stop("caller_state_contradicts_records", detail=contradictions)
    if state["issues"]:
        return stop("records_inconsistent", detail=state["issues"])
    if state["recoverable"]:
        return stop("recovery_required", detail=state["recoverable"])
    if remaining <= 0:
        return stop("target_met")
    review = state.get("review") or {}
    if review.get("blocking"):
        return stop("operator_review_required", detail=review)
    if state["requests_made"] >= ceiling:
        return stop("request_ceiling_reached")
    if state["requests_made"] >= limits["base_requests"] and not review.get("authorised"):
        return stop("retry_requires_operator_authorization",
                    detail={"next_request": state["requests_made"] + 1,
                            "base_requests": limits["base_requests"]})
    requested = inputs.get("duration_seconds")
    if requested is None or abs(float(requested) - limits["seconds_per_generation"]) > 1e-6:
        return stop("request_length_differs_from_approved_plan",
                    detail={"requested": requested,
                            "approved": limits["seconds_per_generation"]})
    try:
        cost = _unit_cost(tool, inputs)
    except PaidCostUnavailable as exc:
        return stop("unpriced", detail=str(exc))
    checks["next_call_usd"] = cost
    if cost > limits["allocation_usd"] - spent + 1e-9:
        return stop("tool_allocation_exhausted")
    if cost > tracker.usable_budget_usd + 1e-9:
        return stop("budget_would_be_exceeded")
    return {"generate": True, "reason": "more_accepted_music_needed", "checks": checks}


def _process_candidates(
    *, candidates: list[Mapping[str, Any]], screen: Mapping[str, Any],
    evaluate: Any, probe: Any,
) -> list[dict[str, Any]]:
    rows = []
    for cand in candidates:
        cand = dict(cand)
        measured = _safe_probe(probe, cand.get("path")) if cand.get("downloaded") else None
        measurements = None
        if screen.get("thresholds") and cand.get("downloaded"):
            try:
                measurements = screen["measure"](cand["path"])
            except Exception:
                measurements = None
        cand["measured_seconds"] = measured
        cand["screen"] = screen_music_candidate(
            cand, prohibited_terms=screen["prohibited_terms"],
            fields=screen.get("fields", ("title", "tags")),
            compliance_terms=screen.get("compliance_terms", ()),
            measured_seconds=measured, measurements=measurements,
            thresholds=screen.get("thresholds"),
        )
        rows.append(cand)
    try:
        evaluations = evaluate(rows) or {}
    except Exception as exc:  # an evaluation that fails decides nothing
        evaluations = {c["index"]: {"outcome": UNCERTAIN, "criterion": "evaluation_failed",
                                    "reason": str(exc)} for c in rows}
    out = []
    for cand in rows:
        decision = decide_music_candidate(cand, cand["screen"], evaluations.get(cand["index"]),
                                          prohibited_terms=screen["prohibited_terms"])
        accepted = decision["outcome"] == ACCEPTED
        out.append({
            "index": cand["index"], "path": cand.get("path"), "title": cand.get("title"),
            "tags": cand.get("tags"), "downloaded": bool(cand.get("downloaded")),
            "provider_reported_seconds": cand.get("duration_seconds"),
            "measured_seconds": cand["measured_seconds"],
            "outcome": decision["outcome"], "criterion": decision.get("criterion"),
            "evidence": decision.get("evidence"), "reason": decision.get("reason", ""),
            "screen": cand["screen"],
            "accepted_seconds": cand["measured_seconds"] if accepted else 0.0,
        })
    return out


def _validate_screen(screen: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not screen or "prohibited_terms" not in screen:
        raise ValueError(
            "screen must state prohibited_terms (from the channel's BRAND.md) - "
            "paid music is never generated without a bounded screen")
    if screen.get("thresholds") and not callable(screen.get("measure")):
        raise ValueError("screen.thresholds needs screen.measure(path) -> {metric: value}")
    return screen


def generate_music_programme(
    *,
    project_dir: Path,
    proposal_packet: Mapping[str, Any],
    tracker: Any,
    tool: Any,
    inputs: Mapping[str, Any],
    evaluate: Any,
    screen: Mapping[str, Any],
    max_requests: Optional[int] = None,
    accepted_seconds: Optional[float] = None,
    requests_made: Optional[int] = None,
    probe: Optional[Any] = None,
) -> dict[str, Any]:
    """Generate until the approved unique-music target is met - and no further.

    Before EVERY paid request, progress is rebuilt from the project's durable
    records (`reconcile_music_progress`) and the request is authorised against
    the approved proposal (`authorize_music_request`). A billed task that was
    interrupted is recovered with the tool's free ``fetch`` first, never paid
    for again. Every candidate goes through the bounded screen and the
    caller's ``evaluate(candidates) -> {index: evaluation}``.

    Stops - and makes no further paid call - on: target met; a request whose
    candidates are all rejected or any uncertain (operator review); a request
    from the retry allowance without operator authorisation; the approved
    request ceiling; this tool's approved allocation or the budget cap; a
    refused reservation; a pricing mismatch; a provider failure; records that
    disagree; a charge whose outcome is unknown; or a caller's
    ``requests_made`` / ``accepted_seconds`` that the records contradict.
    """
    from lib import paid_call_guard

    if probe is None:
        from tools.analysis.audio_probe import probe_duration as probe
    screen = _validate_screen(screen)
    project_dir = Path(project_dir)
    _require_project_tracker(tracker, project_dir)
    limits = approved_music_limits(proposal_packet, tool.name)
    try:
        with _paid_audio_lock(project_dir):
            return _music_programme_locked(
                project_dir=project_dir, limits=limits, tracker=tracker, tool=tool,
                inputs=inputs, evaluate=evaluate, screen=screen, max_requests=max_requests,
                accepted_seconds=accepted_seconds, requests_made=requests_made, probe=probe,
                guard=paid_call_guard)
    except PaidAudioInProgress as exc:
        return {"target_seconds": limits["target_seconds"], "requests_made": None,
                "accepted_seconds": None, "spent_usd": round(tracker.budget_spent_usd, 4),
                "limits": limits, "generations": [],
                "stop": {"generate": False, "reason": "paid_audio_in_progress", "detail": str(exc)},
                "ledger_path": str(project_dir / MUSIC_LEDGER)}


def _music_programme_locked(*, project_dir: Path, limits: Mapping[str, Any], tracker: Any,
                            tool: Any, inputs: Mapping[str, Any], evaluate: Any,
                            screen: Mapping[str, Any], max_requests: Optional[int],
                            accepted_seconds: Optional[float], requests_made: Optional[int],
                            probe: Any, guard: Any) -> dict[str, Any]:
    base = Path(inputs["output_path"])
    ledger = load_music_ledger(project_dir)
    if ledger.get("output_base") and Path(ledger["output_base"]) != base:
        raise ValueError(f"this programme writes to {ledger['output_base']}, not {base}")
    ledger.update(tool=tool.name, output_base=str(base), limits=limits)
    _save_music_ledger(project_dir, ledger)

    generations: list[dict[str, Any]] = []
    attempted_recovery: set[int] = set()
    claimed = {"claimed_requests_made": requests_made, "claimed_accepted_seconds": accepted_seconds}
    stop: dict[str, Any] = {}
    while True:
        _settle_reviewed_charges(project_dir, tracker, tool.name)
        state = reconcile_music_progress(project_dir=project_dir, tool=tool,
                                         cost_entries=tracker.entries, output_base=base,
                                         probe=probe)
        pending = [r for r in state["recoverable"] if r["request"] not in attempted_recovery]
        if pending and not state["issues"] and not claimed_contradiction(state, claimed):
            item = pending[0]
            attempted_recovery.add(item["request"])
            outcome = _recover_music_request(project_dir=project_dir, tracker=tracker, tool=tool,
                                             inputs=inputs, item=item, screen=screen,
                                             evaluate=evaluate, probe=probe)
            generations.append(outcome)
            if not outcome.get("success"):
                stop = {"generate": False, "reason": "recovery_failed", "detail": outcome}
                break
            continue

        decision = authorize_music_request(state=state, limits=limits, tracker=tracker, tool=tool,
                                           inputs=inputs, max_requests=max_requests, **claimed)
        claimed = {"claimed_requests_made": None, "claimed_accepted_seconds": None}
        if not decision["generate"]:
            stop = decision
            break

        n = state["next_request"]
        out = _request_path(base, n)
        operation = f"music generation {n}"
        ledger = load_music_ledger(project_dir)
        ledger["requests"].append({
            "request": n, "operation": operation, "output_path": str(out),
            "status": "submitting", "authorization": decision["checks"],
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_music_ledger(project_dir, ledger)
        try:
            with guard.grant(tool=tool.name, operation=operation, output_path=out,
                             kind="music", request=n):
                result = tracker.run_tool(tool, {**dict(inputs), "output_path": str(out)},
                                          operation=operation)
        except (BudgetExceededError, ApprovalRequiredError) as exc:
            _update_request(project_dir, n, status="not_executed", detail=str(exc))
            stop = {**decision, "generate": False, "reason": "reservation_refused",
                    "detail": str(exc)}
            break
        except Exception as exc:
            _update_request(project_dir, n, status="failed", charge_status="unknown",
                            error=f"{type(exc).__name__}: {exc}",
                            cost_usd=_entry_for(tracker, operation).get("actual_usd"))
            stop = {"generate": False, "reason": "provider_exception", "detail": str(exc)}
            break
        record = _record_result(project_dir=project_dir, tracker=tracker, n=n,
                                operation=operation, result=result, screen=screen,
                                evaluate=evaluate, probe=probe)
        generations.append(record)
        if (result.data or {}).get("pricing_mismatch"):
            stop = {"generate": False, "reason": "pricing_mismatch",
                    "detail": result.data["pricing_mismatch"]}
            break
        if not result.success:
            stop = {"generate": False, "reason": "provider_failure", "detail": result.error}
            break

    final = reconcile_music_progress(project_dir=project_dir, tool=tool,
                                     cost_entries=tracker.entries, output_base=base, probe=probe)
    return {
        "target_seconds": limits["target_seconds"],
        "accepted_seconds": final["accepted_seconds"],
        "remaining_seconds": round(max(0.0, limits["target_seconds"] - final["accepted_seconds"]), 3),
        "requests_made": final["requests_made"],
        "spent_usd": round(tracker.budget_spent_usd, 4),
        "tool_spent_usd": final["spent_usd"],
        "limits": limits,
        "stop": stop,
        "generations": generations,
        "ledger_path": str(project_dir / MUSIC_LEDGER),
    }


def _settle_reviewed_charges(project_dir: Path, tracker: Any, tool_name: str) -> None:
    """Close reservations whose charge outcome the operator has now established."""
    ledger = load_music_ledger(project_dir)
    costs = _music_cost_entries(tracker.entries, tool_name)
    for n, entries in costs.items():
        outcome = _reviews_charge(ledger, n).get("charge_outcome")
        for entry in entries:
            if not outcome or entry.get("status") != "reserved":
                continue
            note = f"charge outcome recorded by operator review: {outcome}"
            entry["details"] = "; ".join(filter(None, [entry.get("details"), note]))
            if outcome == "charged":
                tracker.reconcile(entry["id"], float(entry.get("estimated_usd", 0.0)), success=True)
            else:
                tracker.refund(entry["id"], reason=note)


def claimed_contradiction(state: Mapping[str, Any], claimed: Mapping[str, Any]) -> bool:
    made = claimed.get("claimed_requests_made")
    secs = claimed.get("claimed_accepted_seconds")
    return (made is not None and int(made) != state["requests_made"]) or (
        secs is not None and abs(float(secs) - state["accepted_seconds"]) > _MEASURE_TOLERANCE_S)


def _entry_for(tracker: Any, operation: str) -> dict[str, Any]:
    return next((e for e in reversed(tracker.entries)
                 if e.get("operation") == operation and e.get("status") != "refunded"), {})


def _update_request(project_dir: Path, n: int, **fields: Any) -> dict[str, Any]:
    ledger = load_music_ledger(project_dir)
    req = next(r for r in ledger["requests"] if int(r["request"]) == n)
    req.update(fields)
    req["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_music_ledger(project_dir, ledger)
    return req


def _record_result(*, project_dir: Path, tracker: Any, n: int, operation: str, result: Any,
                   screen: Mapping[str, Any], evaluate: Any, probe: Any,
                   recovered: bool = False) -> dict[str, Any]:
    data = result.data or {}
    entry = _entry_for(tracker, operation)
    fields: dict[str, Any] = {
        "status": "completed" if result.success else "failed",
        "task_id": data.get("task_id"),
        "charge_status": data.get("charge_status"),
        "cost_usd": entry.get("actual_usd", result.cost_usd),
        "cost_entry_id": entry.get("id"),
    }
    check = data.get("pricing_check") or {}
    if check.get("status") == "unverified":
        fields["charge_verification"] = "unverified"
    if recovered:
        fields["recovered"] = True
    if result.success:
        cands = _process_candidates(candidates=data.get("candidates") or [], screen=screen,
                                    evaluate=evaluate, probe=probe)
        fields["candidates"] = cands
        fields["accepted_seconds"] = round(sum(c["accepted_seconds"] for c in cands), 3)
    else:
        fields["error"] = result.error
    req = _update_request(project_dir, n, **fields)
    return {"request": n, "success": bool(result.success), "cost_usd": fields["cost_usd"],
            "task_id": fields["task_id"], "recovered": recovered,
            "accepted_seconds": fields.get("accepted_seconds", 0.0),
            "candidates": req.get("candidates", [])}


def _recover_music_request(*, project_dir: Path, tracker: Any, tool: Any,
                           inputs: Mapping[str, Any], item: Mapping[str, Any],
                           screen: Mapping[str, Any], evaluate: Any, probe: Any) -> dict[str, Any]:
    """Recover an interrupted, billed request with the tool's FREE fetch."""
    n = item["request"]
    operation = f"music generation {n}"
    ledger = load_music_ledger(project_dir)
    if not any(int(r["request"]) == n for r in ledger["requests"]):
        ledger["requests"].append({"request": n, "operation": operation,
                                   "output_path": item["output_path"], "status": "submitting",
                                   "reconstructed": True})
        _save_music_ledger(project_dir, ledger)
    fetch_inputs = {**dict(inputs), "operation": "fetch", "task_id": item["task_id"],
                    "output_path": item["output_path"]}
    if float(tool.estimate_cost(fetch_inputs)) != 0.0:
        return {"request": n, "success": False, "recovered": False,
                "error": f"{tool.name} prices its recovery operation; refusing to pay for it"}
    result = tracker.run_tool(tool, fetch_inputs, operation=f"music recovery fetch {n}")
    if not result.success:
        _update_request(project_dir, n, recovery_error=result.error)
        return {"request": n, "success": False, "recovered": False, "error": result.error}
    # The interrupted call's reservation is the charge: settle it at the
    # estimate (the known rate) - it was billed, and it must not stay open.
    entry = next((e for e in tracker.entries if e.get("id") == item["cost_entry_id"]), None)
    if entry is not None and entry.get("status") == "reserved":
        entry["details"] = "; ".join(filter(None, [
            entry.get("details"), f"interrupted; task {item['task_id']} recovered by free fetch, "
                                  "charge settled at the approved estimate"]))
        tracker.reconcile(entry["id"], float(entry.get("estimated_usd", 0.0)), success=True)
    result.data = {**(result.data or {}), "task_id": item["task_id"], "charge_status": "charged"}
    result.cost_usd = float((entry or {}).get("actual_usd", 0.0))
    return _record_result(project_dir=project_dir, tracker=tracker, n=n, operation=operation,
                          result=result, screen=screen, evaluate=evaluate, probe=probe,
                          recovered=True)


# --------------------------------------------------------------------------
# Paid SFX: approved sources, counted attempts, retries only with approval
# --------------------------------------------------------------------------
#
# The approved plan (`metadata.paid_audio_plan.sfx`) names each source and how
# many generations it may have; the estimate adds a retry allowance. Every
# paid SFX call is one ATTEMPT at one approved source, recorded in
# `cost_log.json` as ``SFX source <s> attempt <k>`` and in the SFX ledger
# (`work/paid_sfx_ledger.json`) before and after it runs. ElevenLabs returns
# the audio in the response - there is no task to recover - so an interrupted
# or unknown-outcome call stops further SFX until the operator settles it.

SFX_LEDGER = Path("work") / "paid_sfx_ledger.json"
_SFX_OPERATION = re.compile(r"^SFX source (\d+) attempt (\d+)$")


def approved_sfx_limits(proposal_packet: Mapping[str, Any], tool_name: str) -> dict[str, Any]:
    """Sources, per-source counts, retry allowance and USD allocation - from the approval."""
    approval = proposal_packet.get("approval") or {}
    if approval.get("status") not in _APPROVED:
        raise BudgetNotApproved(f"proposal approval is {approval.get('status')!r}")
    plan = ((proposal_packet.get("metadata") or {}).get("paid_audio_plan") or {}).get("sfx")
    if not plan or plan.get("tool") != tool_name:
        raise BudgetNotApproved(f"the approved proposal has no paid_audio_plan.sfx for {tool_name!r}")
    items = [i for i in ((proposal_packet.get("cost_estimate") or {}).get("line_items") or [])
             if i.get("tool") == tool_name]
    base = sum(int(i.get("quantity", 0)) for i in items if i.get("operation") == "SFX generation")
    retry = sum(int(i.get("quantity", 0)) for i in items if i.get("operation") == "SFX retry allowance")
    sources = [{"source": k, "purpose": src.get("purpose"),
                "duration_seconds": src.get("duration_seconds"),
                "count": int(src.get("count", 1)), "loop": src.get("loop")}
               for k, src in enumerate(plan.get("sources") or [], start=1)]
    if not sources or sum(s["count"] for s in sources) != base \
            or int(plan.get("generations", base)) != base \
            or int(plan.get("retry_generations", retry)) != retry:
        raise BudgetNotApproved(
            f"approved SFX plan and estimate disagree ({sum(s['count'] for s in sources)} source "
            f"generations, plan {plan.get('generations')}+{plan.get('retry_generations')}, "
            f"estimate {base}+{retry}); the proposal contradicts itself")
    return {"tool": tool_name, "sources": sources, "base_generations": base,
            "retry_generations": retry, "max_generations": base + retry,
            "allocation_usd": round(sum(float(i.get("estimated_usd", 0)) for i in items), 4)}


def load_sfx_ledger(project_dir: Path) -> dict[str, Any]:
    path = Path(project_dir) / SFX_LEDGER
    if not path.exists():
        return {"version": 1, "calls": [], "reviews": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_sfx_ledger(project_dir: Path, ledger: Mapping[str, Any]) -> None:
    _write_json_atomic(Path(project_dir) / SFX_LEDGER, ledger)


def record_sfx_review(
    project_dir: Path,
    *,
    reviewer: str,
    note: str,
    authorize_retry_source: Optional[int] = None,
    operation: Optional[str] = None,
    charge_outcome: Optional[str] = None,
) -> dict[str, Any]:
    """Record an explicit operator decision about paid SFX.

    ``authorize_retry_source`` permits ONE retry of that approved source
    (still within the approved retry allowance); ``operation`` +
    ``charge_outcome`` (``charged`` / ``not_charged``) settle a call whose
    charge was unknown. Record only what the operator actually decided.
    """
    if not reviewer or not note:
        raise ValueError("a review records who decided and what they said")
    if (operation is None) != (charge_outcome is None) or \
            charge_outcome not in (None, "charged", "not_charged"):
        raise ValueError("settle a call with both operation and charge_outcome "
                         "('charged' or 'not_charged')")
    if authorize_retry_source is None and operation is None:
        raise ValueError("a review must authorise a retry or settle a call")
    ledger = load_sfx_ledger(project_dir)
    review = {"reviewer": reviewer, "note": note,
              "authorize_retry_source": authorize_retry_source,
              "operation": operation, "charge_outcome": charge_outcome,
              "recorded_at": datetime.now(timezone.utc).isoformat()}
    ledger.setdefault("reviews", []).append(review)
    _save_sfx_ledger(project_dir, ledger)
    return review


def reconcile_sfx_progress(*, project_dir: Path, tool_name: str,
                           cost_entries: list[Mapping[str, Any]],
                           limits: Mapping[str, Any]) -> dict[str, Any]:
    """Paid-SFX progress rebuilt from cost_log.json and the SFX ledger."""
    ledger = load_sfx_ledger(project_dir)
    calls = {c["operation"]: c for c in ledger.get("calls", [])}
    settled = {r["operation"]: r["charge_outcome"] for r in ledger.get("reviews", [])
               if r.get("operation")}
    attempts: dict[int, int] = {s["source"]: 0 for s in limits["sources"]}
    issues: list[dict[str, Any]] = []
    for entry in cost_entries:
        if entry.get("tool") != tool_name or not _live(entry):
            continue
        op = str(entry.get("operation", ""))
        match = _SFX_OPERATION.match(op)
        if not match or int(match.group(1)) not in attempts:
            if float(entry.get("estimated_usd", 0.0)) > 0:
                issues.append({"issue": "sfx_call_outside_policy", "operation": op,
                               "entry": entry.get("id"), "actual_usd": entry.get("actual_usd")})
            continue
        attempts[int(match.group(1))] += 1
        call = calls.get(op) or {}
        if op in settled:
            continue
        if entry.get("status") == "reserved":
            issues.append({"issue": "charge_outcome_unknown", "operation": op,
                           "detail": "the call was interrupted; settle it with "
                                     "record_sfx_review(operation=..., charge_outcome=...)"})
        elif call.get("status") in (None, "submitting"):
            issues.append({"issue": "sfx_call_not_in_ledger", "operation": op})
        elif call.get("status") == "failed" and call.get("charge_status") in (None, "unknown"):
            issues.append({"issue": "charge_outcome_unknown", "operation": op,
                           "detail": call.get("error") or "provider failure"})
    retries_used = {s["source"]: max(0, attempts[s["source"]] - s["count"])
                    for s in limits["sources"]}
    authorised: dict[int, int] = {}
    for review in ledger.get("reviews", []):
        if review.get("authorize_retry_source") is not None:
            k = int(review["authorize_retry_source"])
            authorised[k] = authorised.get(k, 0) + 1
    spent = sum(float(e.get("actual_usd", 0.0)) + float(e.get("reserved_usd", 0.0))
                for e in cost_entries if e.get("tool") == tool_name and _live(e))
    return {"attempts": attempts, "retries_used": retries_used,
            "retries_authorised": authorised, "spent_usd": round(spent, 4),
            "issues": issues, "ledger": ledger}


def _settle_reviewed_sfx_charges(project_dir: Path, tracker: Any, tool_name: str) -> None:
    settled = {r["operation"]: r["charge_outcome"]
               for r in load_sfx_ledger(project_dir).get("reviews", []) if r.get("operation")}
    for entry in tracker.entries:
        outcome = settled.get(entry.get("operation"))
        if entry.get("tool") != tool_name or entry.get("status") != "reserved" or not outcome:
            continue
        note = f"charge outcome recorded by operator review: {outcome}"
        entry["details"] = "; ".join(filter(None, [entry.get("details"), note]))
        if outcome == "charged":
            tracker.reconcile(entry["id"], float(entry.get("estimated_usd", 0.0)), success=True)
        else:
            tracker.refund(entry["id"], reason=note)


def generate_sfx_source(
    *,
    project_dir: Path,
    proposal_packet: Mapping[str, Any],
    tracker: Any,
    tool: Any,
    inputs: Mapping[str, Any],
    source: int,
) -> dict[str, Any]:
    """ONE paid attempt at approved SFX ``source`` (1-based) - or a recorded stop.

    Permitted only when every earlier SFX call is accounted for, the source is
    approved, its approved count is not used up - or the operator has
    authorised a retry of it within the approved retry allowance - the
    request matches the approved source's duration (and loop setting), the
    output file does not exist yet, and SFX's own allocation and the budget cap
    both have room. Returns ``{"generated", "reason", "checks", "result",
    "operation"}``; ``result`` is the tool's result when a call was made.
    """
    project_dir = Path(project_dir)
    _require_project_tracker(tracker, project_dir)
    limits = approved_sfx_limits(proposal_packet, tool.name)
    try:
        with _paid_audio_lock(project_dir):
            return _sfx_locked(project_dir=project_dir, limits=limits, tracker=tracker,
                               tool=tool, inputs=inputs, source=int(source))
    except PaidAudioInProgress as exc:
        return {"generated": False, "reason": "paid_audio_in_progress", "detail": str(exc),
                "checks": {}, "result": None, "operation": None}


def _sfx_locked(*, project_dir: Path, limits: Mapping[str, Any], tracker: Any, tool: Any,
                inputs: Mapping[str, Any], source: int) -> dict[str, Any]:
    from lib import paid_call_guard as guard

    _settle_reviewed_sfx_charges(project_dir, tracker, tool.name)
    state = reconcile_sfx_progress(project_dir=project_dir, tool_name=tool.name,
                                   cost_entries=tracker.entries, limits=limits)
    spec = next((s for s in limits["sources"] if s["source"] == source), None)
    checks: dict[str, Any] = {
        "source": source, "approved_sources": len(limits["sources"]),
        "attempts": state["attempts"], "retries_used": state["retries_used"],
        "retry_generations": limits["retry_generations"],
        "tool_allocation_usd": limits["allocation_usd"], "tool_spent_usd": state["spent_usd"],
        "usable_budget_usd": round(tracker.usable_budget_usd, 4),
    }

    def stop(reason: str, **detail: Any) -> dict[str, Any]:
        return {"generated": False, "reason": reason, "checks": checks, "result": None,
                "operation": None, **detail}

    if state["issues"]:
        return stop("records_inconsistent", detail=state["issues"])
    if spec is None:
        return stop("source_not_approved")
    attempt = state["attempts"][source] + 1
    if attempt > spec["count"]:
        if sum(state["retries_used"].values()) >= limits["retry_generations"]:
            return stop("retry_allowance_exhausted")
        if state["retries_authorised"].get(source, 0) <= state["retries_used"][source]:
            return stop("retry_requires_operator_authorization",
                        detail={"source": source, "attempt": attempt})
    requested = inputs.get("duration_seconds")
    if spec["duration_seconds"] is not None and (
            requested is None or abs(float(requested) - float(spec["duration_seconds"])) > 1e-6):
        return stop("request_differs_from_approved_source",
                    detail={"requested": requested, "approved": spec["duration_seconds"]})
    if spec["loop"] is not None and bool(inputs.get("loop", False)) != bool(spec["loop"]):
        return stop("request_differs_from_approved_source",
                    detail={"loop": inputs.get("loop"), "approved_loop": spec["loop"]})
    out = inputs.get("output_path")
    pcm = str(inputs.get("output_format", "")).startswith("pcm_")
    target = (Path(out).with_suffix(".wav") if pcm else Path(out)) if out else None
    if target is None or target.exists():
        return stop("output_exists_or_missing", detail={"output_path": out})
    try:
        cost = _unit_cost(tool, inputs)
    except PaidCostUnavailable as exc:
        return stop("unpriced", detail=str(exc))
    checks["next_call_usd"] = cost
    if cost > limits["allocation_usd"] - state["spent_usd"] + 1e-9:
        return stop("tool_allocation_exhausted")
    if cost > tracker.usable_budget_usd + 1e-9:
        return stop("budget_would_be_exceeded")

    operation = f"SFX source {source} attempt {attempt}"
    ledger = load_sfx_ledger(project_dir)
    ledger["calls"].append({"operation": operation, "source": source, "attempt": attempt,
                            "purpose": spec["purpose"], "output_path": str(out),
                            "status": "submitting", "authorization": checks,
                            "started_at": datetime.now(timezone.utc).isoformat()})
    _save_sfx_ledger(project_dir, ledger)
    fields: dict[str, Any]
    try:
        with guard.grant(tool=tool.name, operation=operation, output_path=out, kind="sfx",
                         source=source):
            result = tracker.run_tool(tool, dict(inputs), operation=operation)
    except (BudgetExceededError, ApprovalRequiredError) as exc:
        _update_sfx_call(project_dir, operation, status="not_executed", detail=str(exc))
        return stop("reservation_refused", detail=str(exc))
    except Exception as exc:
        _update_sfx_call(project_dir, operation, status="failed", charge_status="unknown",
                         error=f"{type(exc).__name__}: {exc}")
        return stop("provider_exception", detail=str(exc))
    data = result.data or {}
    entry = _entry_for(tracker, operation)
    fields = {"status": "completed" if result.success else "failed",
              "charge_status": data.get("charge_status") or ("charged" if result.success else None),
              "cost_usd": entry.get("actual_usd", result.cost_usd),
              "cost_entry_id": entry.get("id"),
              "output": data.get("output") or str(out)}
    if not result.success:
        fields["error"] = result.error
    _update_sfx_call(project_dir, operation, **fields)
    return {"generated": bool(result.success),
            "reason": "generated" if result.success else "provider_failure",
            "checks": checks, "result": result, "operation": operation}


def _update_sfx_call(project_dir: Path, operation: str, **fields: Any) -> None:
    ledger = load_sfx_ledger(project_dir)
    call = next(c for c in ledger["calls"] if c["operation"] == operation)
    call.update(fields)
    call["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_sfx_ledger(project_dir, ledger)
