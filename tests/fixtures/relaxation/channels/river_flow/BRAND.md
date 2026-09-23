---
channel_id: channel_0001
channel_name: River Flow Naturescapes
status: Active
format: Long-form cinematic nature relaxation
pipeline: relaxation
language: en
---

# channel_0001 — River Flow Naturescapes (test fixture)

A fixture channel for the cross-channel tests: flowing water first: rivers, waterfalls and streams inside their forests and valleys.
It exercises the same `relaxation` pipeline as every other channel and
differs only through the blocks below.

## Channel policy (read by the pipeline)

```channel-policy
subjects:
  primary: [flowing water, rivers, waterfalls, streams, rapids]
  allowed: [forest, mountains, valleys, mist, canopy]
  optional: [wildlife, lakes]
  avoid: [people as subject, roads, traffic, buildings, cities, commercial brands, boats, timelapses]
composition:
  camera_movement: required
  static_composition: occasional
audio:
  principal_environment: required
  environment_examples: [water]
  supporting_ambience: required
  detail_layers: optional
narration: none
opening:
  required: true
  composition: ScenicOpening
  bed: "moving water from this episode's own footage"
  text_roles: [brand_signature, welcome_message, episode_line]
```

## Channel mix settings (read by the pipeline)

```channel-mix
reference_role: A1-music
master_target_lufs: -16.0
principal:
  role: A2-water
  offset_db: -29.0
  band_db: [-31.0, -27.0]
  treatment_af: "highshelf=f=4000:g=-6:t=q:w=0.7,lowpass=f=9000:p=2"
supporting_group:
  name: ambience_group
  offset_db: -33.7
  band_db: [-36.0, -32.0]
  members:
    A4-forest: 0.42
    A3-birds: 0.48
    A3-wind: 0.10
approved:
  project: channel_0001__video_0003
  preview: v7a
  date: 2026-09-23
```
