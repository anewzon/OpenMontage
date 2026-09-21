# Relaxation — Executive Producer

You are running the `relaxation` pipeline: a 60–180 minute cinematic
relaxation video built from **local, operator-supplied licensed media**.

It suits rivers, forests, waterfalls, ocean, rain, nature relaxation,
meditation and sleep scenery, and comparable long-form calm formats.

**Subject priorities, look and voice come from the channel's `BRAND.md`, not
from this pipeline.** Establish `channel_id` from the project's folder, load
`Channels\<channel_id>\BRAND.md`, and follow it. Never infer a channel from
conversation, and never carry one channel's identity into another's video.

Read `pipeline_defs/relaxation.yaml` before anything else. Then read
the stage director skill for each stage before doing any work in that stage.

## What makes this pipeline different

Every other footage pipeline in OpenMontage *searches* for clips. This one does
not. The operator has already downloaded licensed footage, music and ambience
into the project folder. Your job is to make the best possible film **out of
exactly what is there**.

That inverts the usual order. `inventory` runs **first** — before `idea` — so
that the creative concept is grounded in what the footage actually contains
rather than in something you imagined and then tried to cast.

Stage order: `inventory → idea → scene_plan → edit → finish → review → compose`

## Hard constraints

- **No paid providers.** Budget is 0.00 USD. Do not call any generative video,
  image, music or TTS provider. If material is missing, say what is missing and
  stop — do not generate a substitute.
- **No narration, no subtitles**, unless `brief.txt` explicitly asks for them.
- **Local assets only.** Never fetch from Pexels, Pixabay, Archive.org or any
  other source. If the operator wants more footage they add files and re-run.
- **Never publish.** Produce the file in `output/`. Uploading is the operator's
  decision, made outside this system.

## Renderer

`render_runtime` is **`ffmpeg`** for the body of the video. This is a deliberate
and binding choice, not a default: a 120-minute 4K timeline is ~216,000 frames,
and Remotion renders frame-by-frame through a browser. That path is correct for
motion graphics and wrong for a two-hour montage.

Remotion is permitted **only** for short declared overlay segments (an opening
title card, a closing card) which are rendered separately and concatenated. If
you use it, log it as a `render_runtime_selection` decision naming both options,
per the governance rule in `AGENT_GUIDE.md`.

Announce the runtime choice to the operator before the compose stage. Do not
silently swap runtimes mid-run.

## Known platform constraints (verified on this installation)

These are measured facts about this OpenMontage install. Design within them.

1. **The `ffmpeg` runtime is concat-only.** `video_compose._compose` trims each
   cut, normalises it, concatenates, and muxes one external audio track. It does
   **not** do `xfade`, `overlay` or `amix`. So:
   - Dissolves come from **`video_stitch`** (`transition: crossfade | fade`),
     applied to the clip groups that need them — not from `video_compose`.
   - Layered audio comes from **`audio_mixer`** (`operation: mix`), pre-mixed
     into a single track that is then passed as `audio_path`.
   - **True V2/V3 video overlay is not available on this path.** Do not promise
     it. Achieve visual layering through grading, shot selection and pacing, or
     render a short overlay segment via Remotion and concatenate it.

2. **`audio_mixer` emits 192 kHz mono.** `loudnorm` leaves the internal rate on
   the output. You MUST normalise the mix to 48 kHz stereo before compose:
   `ffmpeg -i mixed.wav -ar 48000 -ac 2 -c:a pcm_s16le mixed48.wav`
   Skipping this ships a file YouTube will re-encode badly.

3. **NVENC requires FFmpeg 7.1.1 on this machine.** The system FFmpeg is 9.0.1,
   which needs NVIDIA driver ≥ 610.00; the installed driver is 591.86, so NVENC
   fails there with "Driver does not support the required nvenc API version".
   The NVENC-capable build is at
   `D:\VidQwik AI\Tools\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe`.
   Benchmarked on this machine at 4K30, NVENC is only ~15% faster than libx264
   and produces substantially larger files for comparable quality. **Default to
   `libx264 -crf 18 -preset medium`** and treat NVENC as the fast-draft option.

## Checkpoints

Follow `skills/meta/checkpoint-protocol.md`. Stages with
`human_approval_default: true` (`idea`, `scene_plan`, `edit`, `review`) stop and
wait for the operator. Do not run a multi-hour render without approved
`edit_decisions` and a passing `review`.

## Resume

Future sessions must reconstruct state from `work/` artifacts on disk, never
from conversation memory. Call `checkpoint.get_next_stage()` to find where to
resume. Completed render chunks in `work/chunks/` are resumable — never delete
them to "start clean" without asking the operator first.
