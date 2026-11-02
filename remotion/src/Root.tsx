import { Composition } from "remotion";
import { CaptionOverlay, CaptionOverlayProps, defaultCaptionProps } from "./CaptionOverlay";
import { TitleCard, TitleCardProps } from "./scenes/TitleCard";
import { LowerThird, LowerThirdProps } from "./scenes/LowerThird";
import { StatCard, StatCardProps } from "./scenes/StatCard";

export const RemotionRoot: React.FC = () => {
  return (
    <>
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
      <Composition
        id="TitleCard"
        component={TitleCard}
        defaultProps={{ title: "Title", subtitle: "" } as TitleCardProps}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={90}
      />
      <Composition
        id="LowerThird"
        component={LowerThird}
        defaultProps={{ name: "Name", role: "" } as LowerThirdProps}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={120}
      />
      <Composition
        id="StatCard"
        component={StatCard}
        defaultProps={{ value: "0", label: "Label" } as StatCardProps}
        fps={30}
        width={1080}
        height={1920}
        durationInFrames={90}
      />
    </>
  );
};
