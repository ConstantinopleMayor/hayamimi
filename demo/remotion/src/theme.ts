export const COLORS = {
  bg: "#0d0f13",
  panel: "#14171d",
  line: "#2a2f3a",
  cream: "#ece5d8",
  dim: "#b0a898",
  vermilion: "#e04f2f",
} as const;

export const LANG_COLORS: Record<string, string> = {
  ja: "#e04f2f",
  en: "#5b7fd4",
  zh: "#d4a13c",
  ko: "#3fae9d",
};

export const LANG_LABEL: Record<string, { name: string; romaji: string }> = {
  ja: { name: "日本語", romaji: "Nihongo" },
  en: { name: "English", romaji: "Eigo" },
  ko: { name: "한국어", romaji: "Hangugeo" },
  zh: { name: "中文", romaji: "Zhongwen" },
};

export const FONT_DISPLAY =
  '"Shippori Mincho B1", "Yu Mincho", "Hiragino Mincho ProN", serif';
export const FONT_MONO =
  '"Consolas", "SFMono-Regular", "Menlo", monospace';
