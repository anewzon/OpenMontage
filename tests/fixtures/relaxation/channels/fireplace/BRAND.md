---
channel_id: channel_0006
channel_name: Hearthlight
status: Active
format: Fireplace and hearth relaxation
pipeline: relaxation
language: en
---

# channel_0006 — Hearthlight (test fixture)

A fixture channel for the cross-channel tests: a fire in a hearth, watched for hours: flames, embers, the room's warmth; mostly static composition, no journey required.
It exercises the same `relaxation` pipeline as every other channel and
differs only through the blocks below.

## Channel policy (read by the pipeline)

```channel-policy
subjects:
  primary: [fireplace, flames, embers, hearth, wood fire]
  allowed: [cabin interior, candles, window with snow, stone hearth]
  optional: [rain on the window, cat]
  avoid: [people as subject, commercial brands, gas flames, digital fire loops]
composition:
  camera_movement: none
  static_composition: allowed
audio:
  principal_environment: required
  environment_examples: [fire crackle]
  supporting_ambience: optional
  detail_layers: optional
narration: none
opening:
  required: false
```

## Channel mix settings (read by the pipeline)

```channel-mix
reference_role: A2-crackle
master_target_lufs: -16.0
supporting_group:
  name: room_tone
  offset_db: -26.0
  band_db: [-30.0, -22.0]
  members:
    A3-room: 1.0
approved:
  project: fixture_fireplace
```
