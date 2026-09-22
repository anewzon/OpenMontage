# Relaxation — Asset Director (`assets` stage)

Produces: `asset_manifest`

You are cataloguing what the operator actually gave you. Everything downstream
depends on this being honest and complete. A creative plan built on a wrong
inventory produces an unbuildable timeline.

## 1. Validate the project

Confirm these exist under the project root: `brief.txt`, `visuals/`, `music/`,
`sfx/`, `overlays/`, `licenses/`, `work/`, `output/`.

Read `proposal_packet` for the approved concept. Note the target duration — you will report usable footage
against it.

## 2. Probe every file

For every file in `visuals/`, `music/`, `sfx/`, `overlays/`, run ffprobe and
record:

**Video** — path, duration, width × height, fps, codec, pixel format, bitrate,
rotation, whether it has an audio stream.

**Audio** — path, duration, sample rate, channels, codec, integrated loudness.

Do not skip files because the name looks unpromising. Operators do not rename
files and you were told not to require it.

## 3. Flag, do not silently fix

Record these as flags on the asset, and surface them to the operator:

- Below 3840×2160 — usable, but note it. **Never silently upscale.** A 1080p
  clip stretched to 4K looks worse than a 4K timeline with one soft shot.
- Frame rate that is not 30 — note it; the edit director decides conform policy.
- Visible watermark, burned-in logo, or timecode — reject, with reason.
- People as a primary subject, cities, aggressive camera movement — reject by
  default per BRAND.md, unless `brief.txt` overrides.
- Heavy compression artefacts, rolling shutter, or exposure pumping — flag.

## 4. Find the usable sub-ranges

This is the most valuable thing you do. **Do not assume a whole file is usable.**
Stock clips routinely open or close with a camera bump, a focus hunt, or an
exposure shift.

For each visual asset, propose one or more usable in/out ranges that are steady,
well-exposed and clean, and say what each range contains:

- subject (river, canopy, ridge, rain on leaves, …)
- shot scale — wide / medium / detail
- motion — static, slow pan, tracking, aerial, handheld
- light — dawn, overcast, midday, golden, dusk
- approximate dominant colour

Sample frames across each clip to ground this. Do not describe footage you have
not looked at.

## 5. Cross-reference licences

For every asset, record provenance and licence evidence from `licenses/`.

**If an asset has no licence evidence, flag it `licence_unverified` and say so
plainly in your report.** Do not quietly include it and do not quietly drop it —
whether to use it is the operator's call, and it is a publishing risk, not an
editorial one.

## 6. Report the footage budget

Compute and state:

- total usable visual minutes
- total music minutes
- total ambience/SFX minutes
- **usable visual minutes ÷ target duration = the reuse factor**

The reuse factor governs what the creative director can honestly promise. Say it
out loud. As rough guidance for relaxation content: a factor above 0.5 allows a
varied edit; around 0.25 means deliberate, spaced re-use with different
grading/framing; below 0.15 means the target duration should come down or more
footage should come in. State which band you are in and recommend accordingly —
do not just hand over a number.

## Classify every clip's native audio

Source clips often carry their own sound. **Do not blanket-keep it and do not
blanket-mute it** — decide per clip, and record the decision.

For each clip with an audio stream, establish: is the sound genuinely
synchronised to what is visible? Is the recording clean? Does it contain
voices, traffic, handling noise, microphone wind, baked-in music or other
contamination? Does it match the environment the scene plan puts it in?

Then classify:

| Class | Meaning |
|---|---|
| `USE` | Clean, synchronised, matches the scene — may join the water/environment foundation |
| `USE_AFTER_TREATMENT` | Useful content behind a fixable problem (wind rumble, hiss, a little too hot) |
| `REJECT` | Voices, traffic, handling noise, baked-in music, or sound that contradicts the picture |
| `NO_AUDIO` | No audio stream present |

Clean native river or waterfall sound is often **better** than a library bed,
because it is genuinely the water on screen. Useful native birds or wind can
feed the environmental layer.

**Measure, do not guess a level.** Record each usable clip's actual loudness so
the Edit Director can set a real gain. **Never assign a blanket figure such as
"native audio at 10%"** — that is a guess dressed as a decision.

Note the risk explicitly for anything classified `REJECT`: rejected audio must
not reach the master, and the Edit and Compose Directors rely on this
classification to keep it out.

## Reject, don't force

**Not every downloaded file belongs in the video.** The employee downloaded what
`work/ASSET_LIST.md` asked for; some of it will still be wrong — off-brand,
soft, watermarked, wrong mood, or simply weaker than its neighbours.

Rejecting a clip with a stated reason is doing the job. Forcing every file in
because someone paid for it is how a montage ends up incoherent. Say what you
rejected and why, then re-check that the remaining footage budget still supports
the target duration.

For audio, assess where practical: duration, loudness, ambience type, mood, and
whether it matches the visible environment — a gentle-creek bed under a
waterfall is a mismatch, not a soundtrack.

## Output

Write a schema-valid `asset_manifest` to `work/`. Every asset needs `id`, `type`,
`path`, `duration` and a provenance/licence note. Put probe data, usable ranges,
flags and `inventory_stats` (including the reuse factor) under `metadata`.

Keep analysis files in `work/` — the operator never manages them.

Use the **native analysis tools** in the registry — `video_analyzer`, media
probing, frame sampling, scene detection, audio analysis. Do not write ad-hoc
Python analysis when a native tool exists.

Run `skills/meta/reviewer.md`, then checkpoint the `assets` stage carrying the
`asset_manifest` artifact.
