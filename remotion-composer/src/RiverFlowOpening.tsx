// Compatibility alias. The channel opening was first written for one river
// channel and named after it; it was always a generic scenic opening. New
// channels and new productions use ScenicOpening. This module keeps the old
// import name working for anything that still uses it.
export {
  ScenicOpening as RiverFlowOpening,
  calculateScenicOpeningMetadata as calculateRiverFlowOpeningMetadata,
} from "./ScenicOpening";
export type { ScenicOpeningProps as RiverFlowOpeningProps } from "./ScenicOpening";
