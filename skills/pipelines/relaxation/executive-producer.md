# Relaxation — Executive Producer

You are running the `relaxation` pipeline: a cinematic relaxation video at any
approved duration from **60 seconds to 5 hours** (`metadata.duration` in the
manifest).

Media is either operator-supplied licensed footage (`licensed_manual`) or
permitted free stock acquired through OpenMontage's own provider tools
(`free_auto`). The mode comes from `proposal_packet.metadata.sourcing`, and
both keep a human approval gate.

It is a production grammar, not a subject: it suits rivers, forests, ocean,
coastlines, deserts, mountains, lakes, city scenery, city tours without
narration, roads, landmarks, countryside, wildlife, rain, night city,
fireplace and comparable calm formats. It is not for narrated documentaries,
factual travel guides, crime stories or presenter-led shows.

**Editorial scale follows the approved duration**, never a template: movements,
shot count, hold lengths, music, ambience coverage, sourcing quantity, QC
sampling and chunking all scale with it. A 60-second piece and a 5-hour film
are both correct outputs of this pipeline.

Channel identity — subjects, look, audio balance, opening, delivery canvas —
lives in that channel's `BRAND.md`, never in this pipeline. **If the channel
requires a branded opening, it is part of the production**: planned at
proposal, carried through edit, rendered and QC'd at compose.

Read `pipeline_defs/relaxation.yaml` first, then each stage's director skill
**before** doing any work in that stage.

## Stages

`research → proposal → procurement → assets → scene_plan → edit → compose → publish`

Seven are the canonical OpenMontage stages with their canonical artifacts.
`procurement` is the single addition: the footage is licensed stock a human must
buy, and `asset_manifest` cannot represent a request for files that do not exist
yet. It produces no new artifact schema — the request travels in checkpoint
metadata, with a Markdown view for the employee.

## Channel context is loaded at runtime

**This pipeline hard-codes no channel, no brand, no subject and no competitor.**
Resolve the channel from the project id, then load its five policy files
with `lib.channel_policy.load_channel(<VidQwik root>/Channels/<channel_id>,
expected_id=<channel_id>)`: `BRAND.md` (profile frontmatter, the
`channel-policy` block, the `channel-mix` block), `COMPETITORS.md`,
`RESEARCH.md`, `THUMBNAIL.md` and `METADATA.md`. A missing or invalid file is
a channel-policy defect: stop and report it. Subject priorities and the
avoid-list, whether water or camera movement is required, whether a
principal environment layer exists, look, voice, thumbnail grammar and
publishing language all come from there; production behaviour comes from
here. Nothing in this pipeline assumes water, a forest, birds, a journey or
a moving camera - each is a channel's choice. Never carry one channel's identity into another's
video, and never infer a channel from conversation.

## State lives in checkpoints, not in chat

Use the native mechanism exactly as `skills/meta/checkpoint-protocol.md`
documents it:

- `init_project()` once, at the start
- `get_next_stage()` to find where to resume — **this is the authority**
- `write_checkpoint()` per stage with `in_progress`, `awaiting_human`,
  `completed` or `failed`
- artifacts validated against their canonical schemas

A fresh session resumes from `get_next_stage()` and the existing checkpoints,
never from conversational memory. Long stages save partial progress through
checkpoint metadata; completed render chunks in `work/chunks/` are resumable and
must not be deleted to "start clean" without asking.

If a project also carries a `STATUS.md`, it is a **human-readable mirror only**.
If it ever disagrees with a checkpoint, **the checkpoint wins** — correct the
mirror, never the other way round.

## Hard constraints

- **Paid generation only inside the approved budget.** The default budget is
  0.00 USD. Generated music (`suno_music`) and generated SFX (`elevenlabs_sfx`)
  are the only paid providers in this pipeline's plan; they are priced at
  `proposal`, approved by the operator as `approval.approved_budget_usd`, and
  run at `assets` only through `generate_music_programme` and
  `generate_sfx_source`, on `approved_budget_tracker(...)` — OpenMontage's
  `CostTracker` in cap mode, with each tool held to its own approved
  allocation. A call the budget cannot cover
  **stops the run**; it is not a warning. No generative video, image or TTS
  provider is called. If material is missing, say what is missing and stop —
  do not generate an unplanned substitute.
- **No silent provider substitution.** If a planned provider is unavailable,
  report it, show the registry's alternatives with their cost and quality
  differences, and get the operator's choice before switching — logged as a
  `provider_selection` decision.
- **Local assets only.** Never fetch stock from Pexels, Pixabay or Archive.org.
  Web access is for *research* and for finding Envato item pages, not for
  acquiring media.
- **Never auto-download Envato assets.** Licensing and downloading are human
  actions.
- **No narration, no subtitles**, unless the brief explicitly asks.
- **Never publish.** Produce the package in `output/`. Uploading is the
  operator's decision, made outside this system.

## Production mode and platform defects (binding)

Videos are made in **production mode** (`lib/production_mode.py`). The engine
and shared policy are read-only - `lib/`, `tools/`, `pipeline_defs/`,
`skills/`, `schemas/`, the Remotion sources, configuration and permission
files, every channel's `BRAND.md`, `ARCHITECTURE.md` and the operator guide -
and development git (reset, clean, checkout, switch, restore, merge, pull,
push, rebase, commit, stash, tag, branch) is refused. A production session
writes only its own project: artifacts, media, checkpoints and reports under
`projects/<project_id>/`.

**Never patch the engine during a production run** - not a one-line fix, not
a workaround file, not a changed default - and never try to lift a
restriction: do not clear read-only attributes, edit permission files, run
denied git commands another way, or turn production mode off. Only the
operator turns it off, at their own terminal, to develop.

**The defect rule.** When the platform itself is wrong - a tool crashes, a
library returns something impossible, a Director contradicts the code, a
gate cannot be satisfied because of a bug rather than the project:

**platform defect -> checkpoint the current project -> report the defect -> stop**

```python
from lib.production_mode import record_platform_defect
record_platform_defect(project_dir, stage=current_stage, summary="...", evidence="...")
```

It writes `work/defects/<utc>_<stage>.md` and re-writes the stage's
checkpoint as `failed` with its artifacts kept, so the project resumes
exactly there once the engine is fixed in a separate development session.
Then tell the operator what broke, with the evidence, and **end the turn**.

## Verified platform constraints

Measured on this installation — design within them, and re-check each run in
case the toolchain has changed.

1. **The `ffmpeg` runtime is concat-only.** No `xfade`, `overlay` or `amix`.
   Dissolves come from `video_stitch`, layered audio from `audio_mixer`.
   **True V2/V3 video overlay is unavailable** on this path — state the
   limitation rather than implying layering that is not there. Do not build a
   compositor.
2. **`video_stitch` crossfade outputs `yuv444p`** — its `xfade` paths do not pin
   the pixel format. ffprobe every stitch output.
3. **`audio_mixer` emits 192 kHz.** Normalise to 48 kHz stereo before composing.
4. **Single-pass `loudnorm` misses target** (−14 requested → −12.0). Always
   finish with an explicit two-pass loudnorm.
5. **`ffmpeg` and `ffprobe` come from PATH** as ordinary `cmd:` dependencies.
   There is no project-local FFmpeg and no override variable.
6. **CPU encoding is the production path** — `libx264 -crf 18 -preset medium`.
   GPU encoding is an optional convenience for drafts; where NVENC is
   unavailable on the installed driver that is an acceleration limitation, not
   a production blocker. Measure throughput on the machine you are rendering
   on rather than quoting a remembered figure.

## Renderer

`render_runtime` is chosen **in conversation at the `proposal` stage** and
carried unchanged through `edit_decisions` — see `AGENT_GUIDE.md`. Present both
available runtimes with honest tradeoffs, recommend one, log a
`render_runtime_selection` decision naming every option, and wait for approval.
Silent defaults and mid-run swaps are governance violations.

For a full-length body the answer is `ffmpeg`. Remotion is for short declared
overlay segments only.

## Checkpoints

Follow `skills/meta/checkpoint-protocol.md`. Stages with
`human_approval_default: true` — `proposal`, `procurement`, `scene_plan`,
`edit`, `publish` — stop and wait for the operator. At `procurement`, write
`awaiting_human` and **end the turn**. Never start a multi-hour render without
an approved timeline and a passing QC path.
