# Relaxation — Packaging Director (`package` stage)

Produces: `output/thumbnail.jpg`, `output/publish.txt`

## Run this only after the video exists

`output/final.mp4` must exist and have passed QC before this stage begins.
Packaging describes **the video that actually got made**, not the one that was
planned. A title promising golden-hour light over a video that never reached
golden hour is a defect, however good the title is.

Re-read before writing anything:

- the final concept and `work/research.md` (its `Suggested Packaging Direction`
  is a starting note, not a decision)
- `Channels\<channel_id>\BRAND.md` — the channel's voice and rules
- the actual footage used, the actual duration, the strongest visual moments —
  **look at the finished file**, sampling frames across it

## Deliver one, not a menu

Evaluate alternatives internally — several titles, several candidate frames.
**The operator receives exactly one** of each: one title, one description, one
tag set, one hashtag set, one thumbnail. They are not a review board.

---

## Title

- accurately describes the video
- fits the channel per `BRAND.md`
- attractive without misleading clickbait — never promise what the footage does
  not deliver
- natural search language, the way a viewer would actually type it
- leads with the primary environment or experience
- **varies across uploads**

Check the channel's previous upload before committing. If your title is the
same formula with the nouns swapped, write a different one. A recognisable
house style is good; a mechanical template is not.

**Do not copy competitor titles**, reworded or otherwise.

## Description

Ready to paste, natural and useful. Where appropriate:

- a short opening description of the scene
- what the viewer will experience
- relaxation / sleep / study / focus context where genuinely relevant
- the nature environment itself
- the channel's identity
- a few relevant hashtags

Do not keyword-stuff. Do not mention competitors, this research, the pipeline,
or anything internal. The description is for viewers.

## Tags

A concise, relevant, comma-separated set — roughly six to twelve. Not hundreds.

Style: `relaxing river sounds, peaceful nature, forest river, waterfall sounds,
nature relaxation, calming river`

## Hashtags

Three or so, relevant, matching what the video actually is.

---

## Thumbnail → `output/thumbnail.jpg`

**Prefer a real frame from the finished video.** It is honest by construction,
it is free, and it matches what the viewer gets.

Method: sample candidate frames across the video, pick the strongest
composition, then enhance it *restrainedly* — the same philosophy as the grade,
so exposure and contrast, not a different look. Export as JPEG at the video's
resolution (or 1920×1080), quality high, and confirm the file reads back.

For this channel prefer: a beautiful water environment · strong composition ·
cinematic appearance · vivid but natural imagery · minimal clutter · generally
little or no text unless research strongly supports it.

If an AI-generated thumbnail is used instead, it **must accurately represent
the actual video**. A generated scene that never appears in the video is
misleading packaging — do not ship it.

**Do not clone competitor thumbnails.**

---

## `output/publish.txt`

Fill the template at `Projects\TEMPLATE\publish.template.txt` exactly — the
operator copies straight out of it into YouTube Studio. Plain text, no JSON.

Replace every `[...]` placeholder. Leave `Playlist:` blank if undetermined.
`Visibility: Private` is the default — a human decides when to go public.

Pull `Channel ID`, `Channel Name` and `Video ID` from the project's `brief.txt`
and the channel's `BRAND.md`. Never type them from memory.

Use `OPTIONAL NOTES` only when there is something genuinely useful — a caveat
about the footage, a timing suggestion. Otherwise leave it empty. Do not pad.

---

## Final check

`output/` must contain **exactly three files**:

```
final.mp4
thumbnail.jpg
publish.txt
```

Verify that. If anything else is in there — work files, chunk leftovers, an
alternative cut — move it to `work/` or remove it. The operator opens that
folder and must see three files and no decisions to make.

Then close the project out in `STATUS.md`:

```
Stage: COMPLETE
Render: COMPLETE
QC: COMPLETE
Thumbnail: COMPLETE
Publish Package: COMPLETE

NEXT ACTION:
Ready for human review and YouTube upload.
```

Set `Stage: COMPLETE` **only after** verifying all three files exist and are
valid — `final.mp4` probes clean, `thumbnail.jpg` reads back as a JPEG,
`publish.txt` has no unreplaced `[...]` placeholders.

Report the three paths, the chosen title, and anything they should check
before publishing, in the `VIDEO COMPLETE` shape given in the project's
`RUN_PRODUCTION.md`. **Do not upload anything.** Publishing is theirs.
