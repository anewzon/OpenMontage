---
channel_id: channel_0003
channel_name: Quiet Wild
status: Active
format: Long-form wildlife and habitat relaxation
pipeline: relaxation
language: en
---

# channel_0003 — Quiet Wild (test fixture)

A fixture channel for the cross-channel tests: animals in their habitats, watched patiently: grazing herds, birds at water, forest mammals, coastal colonies.
It exercises the same `relaxation` pipeline as every other channel and
differs only through the blocks below.

## Channel policy (read by the pipeline)

```channel-policy
subjects:
  primary: [animals, wildlife, habitats, herds, birds, grazing]
  allowed: [forest, savanna, wetlands, coast, mountains, water]
  optional: [rivers, lakes, rain, snow]
  avoid: [people as subject, vehicles, fences and enclosures, hunting, commercial brands]
composition:
  camera_movement: optional
  static_composition: allowed
audio:
  principal_environment: optional
  environment_examples: [habitat ambience, wildlife sound, wind, water]
  supporting_ambience: optional
  detail_layers: optional
narration: none
opening:
  required: false
```

## Channel mix settings (read by the pipeline)

```channel-mix
reference_role: A1-music
master_target_lufs: -16.0
principal:
  role: A2-habitat
  offset_db: -20.0
  band_db: [-23.0, -17.0]
detail_group:
  name: creature_detail
  offset_db: -30.0
  band_db: [-34.0, -26.0]
  members:
    A5-calls: 1.0
supporting_group:
  name: habitat_support
  offset_db: -26.0
  band_db: [-29.0, -23.0]
  members:
    A3-wind: 0.6
    A4-insects: 0.4
approved:
  project: fixture_wildlife
```
