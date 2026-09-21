# Relaxation — Executive Producer

You are running the `relaxation` pipeline: a 60–180 minute cinematic relaxation
video built from **local, operator-supplied licensed media**.

It suits rivers, forests, waterfalls, ocean, rain, nature relaxation, meditation
and sleep scenery, and comparable long-form calm formats.

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

**This pipeline hard-codes no channel, no brand and no competitor.** Resolve the
channel from the project, then read its `BRAND.md`, `COMPETITORS.md` and
`RESEARCH.md`. Subject priorities, look and voice come from there; production
behaviour comes from here. Never carry one channel's identity into another's
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

- **No paid providers.** Budget 0.00 USD. Do not call any generative video,
  image, music or TTS provider. If material is missing, say what is missing and
  stop — do not generate a substitute.
- **Local assets only.** Never fetch stock from Pexels, Pixabay or Archive.org.
  Web access is for *research* and for finding Envato item pages, not for
  acquiring media.
- **Never auto-download Envato assets.** Licensing and downloading are human
  actions.
- **No narration, no subtitles**, unless the brief explicitly asks.
- **Never publish.** Produce the package in `output/`. Uploading is the
  operator's decision, made outside this system.

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
5. **NVENC needs FFmpeg 7.1.1 here** (`D:\VidQwik AI\Tools\...`); the system
   FFmpeg 9.0.1 requires a newer NVIDIA driver. It is only ~15% faster with far
   larger files — **default to `libx264 -crf 18 -preset medium`**.

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
