export const FPS = 30;

export type EventItem = {
  type: "partial" | "final" | "translation" | "refine";
  text: string;
  lang?: string;
  speaker?: string;
  latency_ms?: number;
  tier?: string;
  t: number;
};

export type Segment = {
  lang: string;
  start: number;
  end: number;
  src: string;
};

// Section timing, tuned against the real events.json / segments.json so that
// no final/refine event is cut off, while keeping each section's own audio
// slice inside (or very close to) that language's own speech so we don't
// hear the wrong language playing under the wrong title card.
//
// displayStart/displayEnd are in demo_audio.wav-absolute seconds. Each
// section = a 0.7s title interstitial + the "replay" (captions + audio).
//
// Sections were re-captured with 3.4s language-boundary gaps so every
// refine event lands before the next language begins: no audio clipping.
export const SECTIONS: {
  lang: "ja" | "en" | "ko" | "zh";
  displayStart: number;
  displayEnd: number;
}[] = [
  { lang: "ja", displayStart: 0.3, displayEnd: 26.0 },
  { lang: "en", displayStart: 26.0, displayEnd: 45.9 },
  { lang: "ko", displayStart: 45.9, displayEnd: 67.6 },
  { lang: "zh", displayStart: 67.6, displayEnd: 85.8 },
];

export const TITLE_CARD_SECONDS = 0.7;

// Matches the real manim clip durations in public/manim/ (intro.mp4 4.7s,
// arch.mp4 11.0s, outro.mp4 6.8s, all 1920x1080@30fps).
export const INTRO_SECONDS = 4.7;
export const ARCH_SECONDS = 11.0;
export const OUTRO_SECONDS = 6.8;

export const sectionAudioRange = (
  lang: string,
  segments: Segment[],
  displayStart: number,
  displayEnd: number
): { start: number; end: number } => {
  const own = segments.filter((s) => s.lang === lang);
  const lastEnd = own.length > 0 ? Math.max(...own.map((s) => s.end)) : displayEnd;
  const pad = 0.3;
  return {
    start: displayStart,
    end: Math.min(displayEnd, lastEnd + pad),
  };
};

export const sec = (s: number) => Math.round(s * FPS);
