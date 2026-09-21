import {
  AbsoluteFill,
  CalculateMetadataFunction,
  OffthreadVideo,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { getVideoMetadata } from "@remotion/media-utils";
import { loadFont } from "@remotion/google-fonts/PlayfairDisplay";
import { resolveAsset } from "./lib/resolveAsset";

// Restrained editorial serif at a normal weight. The brand opening is
// deliberately quiet: opacity and scale only, no letter-by-letter reveal,
// no underline, no glow. See Channels/channel_0001/BRAND.md.
const { fontFamily } = loadFont("normal", {
  weights: ["400"],
  subsets: ["latin"],
});

export type RiverFlowOpeningProps = {
  /** Episode footage the wordmark sits over. */
  videoSrc: string;
  /** Channel wordmark. Constant across episodes. */
  wordmark: string;
  /** Optional smaller line beneath (location, season). Changes per episode. */
  episodeLine?: string;
  /** How long the opening runs, in seconds. */
  durationSeconds?: number;
  /** 0-1. How far the footage is darkened so the type stays legible. */
  scrimOpacity?: number;
};

export const calculateRiverFlowOpeningMetadata: CalculateMetadataFunction<
  RiverFlowOpeningProps
> = async ({ props }) => {
  const fps = 30;
  const seconds = props.durationSeconds ?? 8;
  let width = 1920;
  let height = 1080;
  try {
    if (props.videoSrc) {
      const meta = await getVideoMetadata(resolveAsset(props.videoSrc));
      width = meta.width;
      height = meta.height;
    }
  } catch {
    // Fall back to 1080p if the bed cannot be probed.
  }
  return { durationInFrames: Math.round(seconds * fps), fps, width, height };
};

export const RiverFlowOpening: React.FC<RiverFlowOpeningProps> = ({
  videoSrc,
  wordmark,
  episodeLine,
  scrimOpacity = 0.38,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width } = useVideoConfig();

  // Type fades up over ~1.6s and settles from 1.04 -> 1.00 scale.
  const inEnd = Math.round(1.6 * fps);
  const outStart = durationInFrames - Math.round(1.6 * fps);

  const fadeIn = interpolate(frame, [Math.round(0.4 * fps), inEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [outStart, durationInFrames - 4], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = Math.min(fadeIn, fadeOut);

  const scale = interpolate(frame, [Math.round(0.4 * fps), inEnd], [1.04, 1.0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // The scrim itself eases in so the cut into the opening is not abrupt.
  const scrim =
    scrimOpacity *
    interpolate(frame, [0, Math.round(0.6 * fps)], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

  const titleSize = Math.round(width * 0.042);
  const lineSize = Math.round(width * 0.0145);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {videoSrc ? (
        <OffthreadVideo src={resolveAsset(videoSrc)} muted />
      ) : null}

      {/* Gentle vertical scrim - darker in the middle band where the type sits,
          so the words stay legible without a hard box behind them. */}
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg,
            rgba(0,0,0,${scrim * 0.55}) 0%,
            rgba(0,0,0,${scrim}) 45%,
            rgba(0,0,0,${scrim * 0.75}) 100%)`,
        }}
      />

      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          opacity,
          transform: `scale(${scale})`,
        }}
      >
        <div
          style={{
            fontFamily,
            fontWeight: 400,
            fontSize: titleSize,
            letterSpacing: titleSize * 0.14,
            // letter-spacing adds a trailing gap; nudge back so it reads centred
            marginLeft: titleSize * 0.14,
            color: "#F4F2ED",
            textAlign: "center",
            lineHeight: 1.15,
            textShadow: "0 2px 24px rgba(0,0,0,0.45)",
          }}
        >
          {wordmark}
        </div>

        {episodeLine ? (
          <div
            style={{
              fontFamily,
              fontWeight: 400,
              fontSize: lineSize,
              letterSpacing: lineSize * 0.34,
              marginLeft: lineSize * 0.34,
              marginTop: titleSize * 0.46,
              color: "rgba(244,242,237,0.72)",
              textAlign: "center",
              textTransform: "uppercase",
              textShadow: "0 2px 18px rgba(0,0,0,0.5)",
            }}
          >
            {episodeLine}
          </div>
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
