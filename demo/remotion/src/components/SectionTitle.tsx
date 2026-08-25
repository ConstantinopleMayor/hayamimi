import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { COLORS, FONT_DISPLAY, FONT_MONO, LANG_COLORS, LANG_LABEL } from "../theme";

export const SectionTitle: React.FC<{ lang: "ja" | "en" | "ko" | "zh" }> = ({
  lang,
}) => {
  const frame = useCurrentFrame();
  const color = LANG_COLORS[lang];
  const { name, romaji } = LANG_LABEL[lang];

  const scale = interpolate(frame, [0, 10], [0.92, 1], {
    extrapolateRight: "clamp",
  });
  const opacity = interpolate(frame, [0, 6], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        background: COLORS.bg,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          opacity,
          transform: `scale(${scale})`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <div
          style={{
            width: 84,
            height: 6,
            background: color,
            marginBottom: 36,
          }}
        />
        <div
          style={{
            fontFamily: FONT_DISPLAY,
            fontSize: 156,
            fontWeight: 700,
            color,
            letterSpacing: 6,
          }}
        >
          {name}
        </div>
        <div
          style={{
            fontFamily: FONT_MONO,
            fontSize: 30,
            color: COLORS.dim,
            marginTop: 24,
            letterSpacing: 6,
            textTransform: "uppercase",
          }}
        >
          {romaji}
        </div>
      </div>
    </div>
  );
};
