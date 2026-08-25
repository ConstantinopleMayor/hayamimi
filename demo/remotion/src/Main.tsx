import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame } from "remotion";
import eventsData from "../public/events.json";
import segmentsData from "../public/segments.json";
import { COLORS } from "./theme";
import {
  ARCH_SECONDS,
  EventItem,
  FPS,
  INTRO_SECONDS,
  OUTRO_SECONDS,
  Segment,
  SECTIONS,
  TITLE_CARD_SECONDS,
  sec,
  sectionAudioRange,
} from "./timeline";
import { VideoSlot } from "./components/VideoSlot";
import { SectionTitle } from "./components/SectionTitle";
import { ReplayUI } from "./components/ReplayUI";
import { Header } from "./components/Header";

const ALL_EVENTS = eventsData as EventItem[];
const SEGMENTS = segmentsData as Segment[];

const INTRO_FRAMES = sec(INTRO_SECONDS);
const ARCH_FRAMES = sec(ARCH_SECONDS);
const OUTRO_FRAMES = sec(OUTRO_SECONDS);
const TITLE_FRAMES = sec(TITLE_CARD_SECONDS);

const sectionFrameList = SECTIONS.map((s) => ({
  ...s,
  frames: sec(s.displayEnd - s.displayStart),
}));

const sectionsTotalFrames = sectionFrameList.reduce(
  (acc, s) => acc + s.frames,
  0
);

export const Main: React.FC = () => {
  const frame = useCurrentFrame();

  // Map the current global frame to an "audio cursor" (demo_audio.wav-
  // absolute seconds) so the persistent header can compute a running mean
  // latency from every final seen so far, across section boundaries.
  let audioCursor: number | null = null;
  if (frame < INTRO_FRAMES + ARCH_FRAMES) {
    audioCursor = null; // nothing spoken yet
  } else {
    let cursor = frame - INTRO_FRAMES - ARCH_FRAMES;
    if (cursor >= sectionsTotalFrames) {
      audioCursor = SECTIONS[SECTIONS.length - 1].displayEnd;
    } else {
      for (const s of sectionFrameList) {
        if (cursor < s.frames) {
          audioCursor = s.displayStart + cursor / FPS;
          break;
        }
        cursor -= s.frames;
      }
    }
  }

  const finalsSoFar = ALL_EVENTS.filter(
    (e) => e.type === "final" && e.latency_ms !== undefined &&
      audioCursor !== null && e.t <= audioCursor
  );
  const meanLatencyMs =
    finalsSoFar.length > 0
      ? finalsSoFar.reduce((acc, e) => acc + (e.latency_ms ?? 0), 0) /
        finalsSoFar.length
      : null;

  let cursorFrame = 0;
  const introFrom = cursorFrame;
  cursorFrame += INTRO_FRAMES;
  const archFrom = cursorFrame;
  cursorFrame += ARCH_FRAMES;

  const sectionOffsets: number[] = [];
  for (const s of sectionFrameList) {
    sectionOffsets.push(cursorFrame);
    cursorFrame += s.frames;
  }
  const outroFrom = cursorFrame;

  return (
    <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
      <Sequence from={introFrom} durationInFrames={INTRO_FRAMES} name="intro">
        <VideoSlot
          path="manim/intro.mp4"
          eyebrow="hayamimi"
          title="早耳"
          subtitle="live speech, translated in real time"
        />
      </Sequence>

      <Sequence from={archFrom} durationInFrames={ARCH_FRAMES} name="arch">
        <VideoSlot
          path="manim/arch.mp4"
          eyebrow="architecture"
          title="仕組み"
          subtitle="asr -> refine -> translate"
        />
      </Sequence>

      {sectionFrameList.map((s, i) => {
        const events = ALL_EVENTS.filter(
          (e) => e.t >= s.displayStart && e.t <= s.displayEnd
        );
        const audioRange = sectionAudioRange(
          s.lang,
          SEGMENTS,
          s.displayStart,
          s.displayEnd
        );
        const replayFrames = s.frames - TITLE_FRAMES;
        const audioEndFrame = sec(audioRange.end);

        return (
          <Sequence
            key={s.lang}
            from={sectionOffsets[i]}
            durationInFrames={s.frames}
            name={`section-${s.lang}`}
          >
            <Sequence from={0} durationInFrames={TITLE_FRAMES} name="title">
              <SectionTitle lang={s.lang} />
            </Sequence>
            <Sequence
              from={TITLE_FRAMES}
              durationInFrames={Math.max(1, replayFrames)}
              name="replay"
            >
              <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
                <ReplayUI events={events} timeOffsetSeconds={s.displayStart + TITLE_CARD_SECONDS}  lang={s.lang} />
              </AbsoluteFill>
            </Sequence>
            <Audio
              src={staticFile("demo_audio.wav")}
              startFrom={sec(s.displayStart)}
              endAt={audioEndFrame}
            />
          </Sequence>
        );
      })}

      <Sequence from={outroFrom} durationInFrames={OUTRO_FRAMES} name="outro">
        <VideoSlot
          path="manim/outro.mp4"
          eyebrow="hayamimi"
          title="ありがとう"
          subtitle="thank you for watching"
        />
      </Sequence>

      <Header meanLatencyMs={meanLatencyMs} />
    </AbsoluteFill>
  );
};
