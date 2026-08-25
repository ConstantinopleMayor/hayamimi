import React from "react";
import { AbsoluteFill, OffthreadVideo, staticFile } from "remotion";
import { useAssetExists } from "../useAssetExists";
import { FallbackCard } from "./FallbackCard";

export const VideoSlot: React.FC<{
  path: string;
  eyebrow: string;
  title: string;
  subtitle: string;
}> = ({ path, eyebrow, title, subtitle }) => {
  const exists = useAssetExists(path);

  if (exists === false || exists === null) {
    return (
      <AbsoluteFill>
        <FallbackCard eyebrow={eyebrow} title={title} subtitle={subtitle} />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <OffthreadVideo
        src={staticFile(path)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
        onError={() => {
          // swallow — asset-exists check already guards the common case
        }}
      />
    </AbsoluteFill>
  );
};
