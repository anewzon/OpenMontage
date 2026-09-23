---
channel_id: channel_0005
channel_name: Deep Canopy
status: Active
format: Forest-first long-form relaxation
pipeline: relaxation
language: en
---

# channel_0005 — Deep Canopy (test fixture)

A fixture channel for the cross-channel tests: forests first: canopy, trunks, undergrowth, light through leaves, mist between trees; water is welcome, never required.
It exercises the same `relaxation` pipeline as every other channel and
differs only through the blocks below.

## Channel policy (read by the pipeline)

```channel-policy
subjects:
  primary: [forest, canopy, trees, undergrowth, woodland, mist between trees]
  allowed: [streams, birds, fog, snow, moss, wildlife]
  optional: [water, mountains]
  avoid: [people as subject, roads, buildings, logging, commercial brands]
composition:
  camera_movement: preferred
  static_composition: occasional
audio:
  principal_environment: optional
  environment_examples: [forest ambience, wind in leaves, rain, birds]
  supporting_ambience: required
  detail_layers: optional
narration: none
opening:
  required: true
  composition: ScenicOpening
  bed: "light moving through this episode's canopy"
  text_roles: [brand_signature, welcome_message, episode_line]
```

## Channel mix settings (read by the pipeline)

```channel-mix
reference_role: A1-music
master_target_lufs: -16.0
principal:
  role: A2-forest
  offset_db: -22.0
  band_db: [-25.0, -19.0]
supporting_group:
  name: forest_detail
  offset_db: -30.0
  band_db: [-33.0, -27.0]
  members:
    A3-birds: 0.6
    A4-wind: 0.4
approved:
  project: fixture_forest
```
