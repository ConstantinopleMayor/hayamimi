import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { COLORS, FONT_DISPLAY, FONT_MONO, LANG_COLORS, fontFor } from "../theme";
import { EventItem, FPS } from "../timeline";

const LangBadge: React.FC<{ lang?: string }> = ({ lang }) => {
  const color = lang ? LANG_COLORS[lang] ?? COLORS.dim : COLORS.dim;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: FONT_MONO,
        fontSize: 18,
        fontWeight: 700,
        color: "#0d0f13",
        background: color,
        borderRadius: 5,
        padding: "3px 9px",
        letterSpacing: 1,
        textTransform: "uppercase",
      }}
    >
      {lang ?? "??"}
    </span>
  );
};

const SpeakerChip: React.FC<{ speaker?: string }> = ({ speaker }) => {
  if (!speaker) return null;
  return (
    <span
      style={{
        fontFamily: FONT_MONO,
        fontSize: 17,
        color: COLORS.dim,
        border: `1px solid ${COLORS.line}`,
        borderRadius: 5,
        padding: "2px 8px",
      }}
    >
      {speaker}
    </span>
  );
};

export const ReplayUI: React.FC<{
  events: EventItem[];
  timeOffsetSeconds: number;
  lang?: string;
}> = ({ events, timeOffsetSeconds, lang }) => {
  const frame = useCurrentFrame();
  const absoluteTime = timeOffsetSeconds + frame / FPS;

  const priorFinals = events.filter(
    (e) => e.type === "final" && e.t <= absoluteTime
  );
  const lastFinal = priorFinals[priorFinals.length - 1];
  const lastFinalT = lastFinal ? lastFinal.t : -Infinity;

  const partialsAfterLastFinal = events.filter(
    (e) => e.type === "partial" && e.t <= absoluteTime && e.t > lastFinalT
  );
  const currentPartial = partialsAfterLastFinal[partialsAfterLastFinal.length - 1];

  const visibleFinals = priorFinals.slice(-3);

  const translationsAfterLastFinal = events.filter(
    (e) => e.type === "translation" && e.t > lastFinalT && e.t <= absoluteTime
  );
  const translation =
    translationsAfterLastFinal[translationsAfterLastFinal.length - 1];

  const refines = events
    .filter((e) => e.type === "refine" && e.t <= absoluteTime)
    .slice(-2);

  const caretOn = Math.floor(frame / 15) % 2 === 0;

  const partialOpacity = interpolate(
    currentPartial ? absoluteTime - currentPartial.t : 999,
    [0, 0.25],
    [0.4, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      {/* top strip: live partial */}
      <div
        style={{
          position: "absolute",
          top: 104,
          left: 60,
          right: 60,
          height: 210,
          overflow: "hidden",
          background: COLORS.panel,
          border: `1px solid ${COLORS.line}`,
          borderRadius: 14,
          padding: "24px 32px",
          opacity: currentPartial ? 1 : 0,
        }}
      >
        <div
          style={{
            fontFamily: FONT_MONO,
            fontSize: 18,
            color: COLORS.vermilion,
            letterSpacing: 3,
            marginBottom: 10,
          }}
        >
          いま聞き取り中
        </div>
        <div
          style={{
            fontFamily: fontFor(lang),
            // two lines must fit inside the fixed 210px strip: shrink with length
            fontSize: (currentPartial?.text?.length ?? 0) > 120 ? 30 : (currentPartial?.text?.length ?? 0) > 64 ? 38 : 50,
            color: COLORS.cream,
            lineHeight: 1.25,
            opacity: partialOpacity,
          }}
        >
          {currentPartial?.text ?? ""}
          <span
            style={{
              display: "inline-block",
              width: 6,
              height: 46,
              marginLeft: 8,
              background: COLORS.vermilion,
              opacity: caretOn ? 1 : 0,
              verticalAlign: "-8px",
            }}
          />
        </div>
      </div>

      {/* center feed: finals */}
      <div
        style={{
          position: "absolute",
          top: 340,
          left: 60,
          right: 60,
          display: "flex",
          flexDirection: "column",
          gap: 18,
        }}
      >
        {visibleFinals.map((f, idx) => {
          const age = absoluteTime - f.t;
          const ty = interpolate(age, [0, 0.4], [26, 0], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const enter = interpolate(age, [0, 0.3], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const rankFade = [0.55, 0.8, 1][
            idx + (3 - visibleFinals.length)
          ] ?? 1;
          const isLast = idx === visibleFinals.length - 1;

          return (
            <div key={`${f.t}-${idx}`} style={{ opacity: enter * rankFade }}>
              <div
                style={{
                  transform: `translateY(${ty}px)`,
                  background: COLORS.panel,
                  border: `1px solid ${COLORS.line}`,
                  borderRadius: 14,
                  padding: "20px 28px",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 16,
                }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
                  <LangBadge lang={f.lang} />
                  <SpeakerChip speaker={f.speaker} />
                </div>
                <div
                  style={{
                    fontFamily: fontFor(f.lang),
                    fontSize: 44,
                    color: COLORS.cream,
                    lineHeight: 1.3,
                    flex: 1,
                  }}
                >
                  {f.text}
                </div>
                {f.latency_ms !== undefined ? (
                  <div
                    style={{
                      fontFamily: FONT_MONO,
                      fontSize: 22,
                      color: COLORS.dim,
                      whiteSpace: "nowrap",
                      marginTop: 6,
                    }}
                  >
                    {Math.round(f.latency_ms)}ms
                  </div>
                ) : null}
              </div>
              {isLast && translation ? (
                <div
                  style={{
                    fontFamily: FONT_MONO,
                    fontSize: 26,
                    color: COLORS.dim,
                    padding: "10px 28px 0 96px",
                    opacity: interpolate(
                      absoluteTime - translation.t,
                      [0, 0.3],
                      [0, 1],
                      { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
                    ),
                  }}
                >
                  {"→ "}
                  {translation.lang ?? "en"}
                  {"  "}
                  {translation.text}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* bottom-right: refine (清書) stack */}
      <div
        style={{
          position: "absolute",
          bottom: 56,
          right: 60,
          width: 640,
          display: "flex",
          flexDirection: "column",
          gap: 10,
          alignItems: "flex-end",
        }}
      >
        {refines.map((r, idx) => {
          const age = absoluteTime - r.t;
          const opacity =
            interpolate(age, [0, 0.35], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }) * (idx === refines.length - 1 ? 1 : 0.6);
          return (
            <div
              key={`${r.t}-${idx}`}
              style={{
                opacity,
                background: "rgba(20,23,29,0.85)",
                border: `1px solid ${COLORS.line}`,
                borderRadius: 10,
                padding: "10px 18px",
                textAlign: "right",
                maxWidth: "100%",
              }}
            >
              <div
                style={{
                  fontFamily: FONT_MONO,
                  fontSize: 13,
                  color: COLORS.vermilion,
                  letterSpacing: 2,
                  marginBottom: 4,
                }}
              >
                清書
              </div>
              <div
                style={{
                  fontFamily: fontFor(r.lang),
                  fontSize: 24,
                  color: COLORS.dim,
                  lineHeight: 1.4,
                }}
              >
                {r.text}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
