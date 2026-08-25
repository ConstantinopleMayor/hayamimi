import React from "react";
import { useCurrentFrame } from "remotion";
import { COLORS, FONT_DISPLAY, FONT_MONO } from "../theme";

export const Header: React.FC<{ meanLatencyMs: number | null }> = ({
  meanLatencyMs,
}) => {
  const frame = useCurrentFrame();
  const dotOn = Math.floor(frame / 15) % 2 === 0;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        height: 64,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 40px",
        background: "rgba(13,15,19,0.72)",
        borderBottom: `1px solid ${COLORS.line}`,
        zIndex: 50,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div
          style={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: COLORS.vermilion,
            opacity: dotOn ? 1 : 0.25,
            boxShadow: dotOn ? `0 0 10px ${COLORS.vermilion}` : "none",
          }}
        />
        <div
          style={{
            fontFamily: FONT_DISPLAY,
            fontSize: 26,
            color: COLORS.cream,
            letterSpacing: 3,
            fontWeight: 700,
          }}
        >
          早耳
        </div>
        <div
          style={{
            fontFamily: FONT_MONO,
            fontSize: 15,
            color: COLORS.dim,
            letterSpacing: 3,
            textTransform: "uppercase",
            marginTop: 2,
          }}
        >
          hayamimi
        </div>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontFamily: FONT_MONO,
          fontSize: 16,
          color: COLORS.dim,
          border: `1px solid ${COLORS.line}`,
          borderRadius: 20,
          padding: "6px 16px",
          background: COLORS.panel,
        }}
      >
        <span style={{ letterSpacing: 1 }}>avg latency</span>
        <span style={{ color: COLORS.cream, fontWeight: 700 }}>
          {meanLatencyMs === null ? "—" : `${Math.round(meanLatencyMs)}ms`}
        </span>
      </div>
    </div>
  );
};
