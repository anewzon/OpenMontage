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

// Restrained editorial serif. The opening is deliberately quiet: opacity and
// scale only, staggered by hierarchy. No letter-by-letter reveal, no underline,
// no glow. See Channels/channel_0001/BRAND.md.
const { fontFamily } = loadFont("normal", {
  weights: ["400"],
  subsets: ["latin"],
});

export type RiverFlowOpeningProps = {
  /** Episode footage the type sits over. Water must be visibly moving. */
  videoSrc: string;
  /** Small constant brand signature. NOT the headline. */
  brandSignature: string;
  /** The centre of the frame: a short original welcome line. Varies per episode. */
  welcomeMessage: string;
  /** Smallest line: something specific to this episode's environment. Optional. */
  episodeLine?: string;
  /** How long the opening runs, in seconds. */
  durationSeconds?: number;
  /** 0-1. Kept light on purpose - a heavy scrim is a brand defect. */
  scrimOpacity?: number;
};

export const calculateRiverFlowOpeningMetadata: CalculateMetadataFunction<
  RiverFlowOpeningProps
> = async ({ props }) => {
  const fps = 30;
  const seconds = props.durationSeconds ?? 8;
  let width = 1920;
  let height = 1080;
  if (props.videoSrc) {
    // The canvas comes from the episode's own bed. A bed that cannot be read
    // must fail the render: silently falling back to 1080p would put a 1080p
    // opening in front of a 4K body.
    try {
      const meta = await getVideoMetadata(resolveAsset(props.videoSrc));
      width = meta.width;
      height = meta.height;
    } catch (err) {
      throw new Error(
        `RiverFlowOpening could not read its bed ${props.videoSrc}: ${String(err)}`
      );
    }
  }
  return { durationInFrames: Math.round(seconds * fps), fps, width, height };
};

/** Opacity + scale, staggered by `delaySeconds`. Shared by all three elements. */
const useReveal = (delaySeconds: number, holdOutSeconds: number) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const start = Math.round(delaySeconds * fps);
  const inEnd = start + Math.round(1.5 * fps);
  const outStart = durationInFrames - Math.round(holdOutSeconds * fps);

  const fadeIn = interpolate(frame, [start, inEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [outStart, durationInFrames - 4], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = interpolate(frame, [start, inEnd], [1.035, 1.0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return { opacity: Math.min(fadeIn, fadeOut), scale };
};

export const RiverFlowOpening: React.FC<RiverFlowOpeningProps> = ({
  videoSrc,
  brandSignature,
  welcomeMessage,
  episodeLine,
  scrimOpacity = 0.28,
}) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  // Hierarchy order: signature settles first, the message is the payoff,
  // the episode line arrives last and leaves first.
  const sig = useReveal(0.3, 1.9);
  const msg = useReveal(0.9, 1.6);
  const epi = useReveal(1.6, 2.2);

  const scrim =
    scrimOpacity *
    interpolate(frame, [0, Math.round(0.6 * fps)], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });

  // Type scale encodes the hierarchy: message dominant, signature secondary,
  // episode line smallest.
  const msgSize = Math.round(width * 0.052);
  const sigSize = Math.round(width * 0.0145);
  const epiSize = Math.round(width * 0.0118);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {videoSrc ? <OffthreadVideo src={resolveAsset(videoSrc)} muted /> : null}

      {/* Light centre-weighted scrim - enough for legibility, not a dark box. */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at 50% 50%,
            rgba(0,0,0,${scrim * 1.15}) 0%,
            rgba(0,0,0,${scrim * 0.7}) 45%,
            rgba(0,0,0,${scrim * 0.35}) 100%)`,
        }}
      />

      <AbsoluteFill
        style={{ justifyContent: "center", alignItems: "center", textAlign: "center" }}
      >
        {/* 1 - brand signature: small, quiet, above the message */}
        <div
          style={{
            fontFamily,
            fontSize: sigSize,
            letterSpacing: sigSize * 0.42,
            marginLeft: sigSize * 0.42,
            marginBottom: msgSize * 0.42,
            color: "rgba(244,242,237,0.78)",
            textTransform: "uppercase",
            opacity: sig.opacity,
            transform: `scale(${sig.scale})`,
            textShadow: "0 2px 18px rgba(0,0,0,0.55)",
          }}
        >
          {brandSignature}
        </div>

        {/* 2 - welcome message: the centre of the frame */}
        <div
          style={{
            fontFamily,
            fontSize: msgSize,
            letterSpacing: msgSize * 0.085,
            marginLeft: msgSize * 0.085,
            lineHeight: 1.12,
            color: "#F7F5F1",
            opacity: msg.opacity,
            transform: `scale(${msg.scale})`,
            textShadow: "0 2px 30px rgba(0,0,0,0.5)",
          }}
        >
          {welcomeMessage}
        </div>

        {/* 3 - episode line: smallest, lightest, arrives last */}
        {episodeLine ? (
          <div
            style={{
              fontFamily,
              fontSize: epiSize,
              letterSpacing: epiSize * 0.3,
              marginLeft: epiSize * 0.3,
              marginTop: msgSize * 0.5,
              color: "rgba(244,242,237,0.62)",
              opacity: epi.opacity,
              transform: `scale(${epi.scale})`,
              textShadow: "0 2px 16px rgba(0,0,0,0.55)",
            }}
          >
            {episodeLine}
          </div>
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
