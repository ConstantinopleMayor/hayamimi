import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { COLORS, FONT_DISPLAY, FONT_MONO } from "../theme";

export const FallbackCard: React.FC<{
  eyebrow: string;
  title: string;
  subtitle: string;
}> = ({ eyebrow, title, subtitle }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 15], [0, 1], {
    extrapolateRight: "clamp",
  });
  const rise = interpolate(frame, [0, 20], [16, 0], {
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
        opacity,
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(circle at 50% 42%, rgba(224,79,47,0.08), transparent 60%)",
        }}
      />
      <div
        style={{
          fontFamily: FONT_MONO,
          fontSize: 26,
          letterSpacing: 6,
          color: COLORS.vermilion,
          textTransform: "uppercase",
          marginBottom: 28,
          transform: `translateY(${rise}px)`,
        }}
      >
        {eyebrow}
      </div>
      <div
        style={{
          fontFamily: FONT_DISPLAY,
          fontSize: 132,
          color: COLORS.cream,
          fontWeight: 600,
          letterSpacing: 4,
          transform: `translateY(${rise}px)`,
        }}
      >
        {title}
      </div>
      <div
        style={{
          fontFamily: FONT_MONO,
          fontSize: 28,
          color: COLORS.dim,
          marginTop: 26,
          letterSpacing: 2,
          transform: `translateY(${rise}px)`,
        }}
      >
        {subtitle}
      </div>
      <div
        style={{
          position: "absolute",
          bottom: 64,
          fontFamily: FONT_MONO,
          fontSize: 18,
          color: COLORS.line,
          letterSpacing: 3,
        }}
      >
        [ manim clip pending — placeholder card ]
      </div>
    </div>
  );
};
