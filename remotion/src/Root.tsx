import { Composition } from "remotion";
import { CaptionOverlay, CaptionOverlayProps, defaultCaptionProps } from "./CaptionOverlay";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CaptionOverlay"
      component={CaptionOverlay}
      defaultProps={defaultCaptionProps}
      fps={defaultCaptionProps.fps}
      width={1080}
      height={1920}
      durationInFrames={Math.ceil(defaultCaptionProps.durationSec * defaultCaptionProps.fps)}
      calculateMetadata={({ props }: { props: CaptionOverlayProps }) => ({
        fps: props.fps,
        durationInFrames: Math.max(1, Math.ceil(props.durationSec * props.fps)),
      })}
    />
  );
};
