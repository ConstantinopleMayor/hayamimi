// hayamimi desktop subtitle window
// Transparent, frameless, always-on-top subtitle window rendering hayamimi's
// OBS overlay (http://localhost:8833/ by default) onto the desktop.
//
// NOTE: this is the ROLLED-BACK version (pre auto-width-resize). The
// MutationObserver->resize feedback loop that froze all interaction is gone.
// Features that were confirmed working stay:
//   - single window, buttons injected into the page (no-drag) at top-left
//   - OS-native drag region over the whole body while interactive
//   - lock button  (toggle click-through)
//   - gear button  (settings menu: font size / font / lang / pass-through / quit)
//   - language button (cycle displayed translation EN/ZH/KO/OFF)
//   - translation rendered under the subtitle line (same font size)
//   - click-through polling keeps the button band clickable in pass-through
//   - taskbar icon (focusable:true)
//
// Interaction:
//   Lock ON:  interactive; left-drag anywhere moves the window (native).
//             Right-click / gear opens the menu.
//   Lock OFF: click-through; only the top-left button band stays interactive.
//   Ctrl+Alt+D toggle   Ctrl+Alt+L cycle language (close: the title bar ✕)
const { app, BrowserWindow, screen, globalShortcut, Menu, ipcMain, dialog } = require("electron");
const path = require("path");
const fs = require("fs");

// Quiet Chromium's background network probes (captive-portal connectivity
// checks, component updater, network-quality estimation) that otherwise SLL
// handshake-fail on networks that can't reach Google (the window itself only
// loads http://localhost:8833 -- these errors come from Chromium, not the
// page). Disabling them stops the repeated
// "ssl_client_socket_impl: handshake failed" console spam.
app.commandLine.appendSwitch("disable-background-networking");
app.commandLine.appendSwitch("disable-component-update");

// Diag log lives in %TEMP%: __dirname points inside app.asar when packaged
// (read-only), and the old location was deleted with the dev checkout anyway.
const DIAG = path.join(app.getPath("temp"), "hayamimi-desktop-subtitle.log");
let diagOpen = false;
function diag(msg) {
  try {
    if (!diagOpen) { diagOpen = true; fs.writeFileSync(DIAG, ""); }
    fs.appendFileSync(DIAG, new Date().toISOString() + " " + msg + "\n");
  } catch (_) {}
}

const DEFAULT_SIZE = 24;  // 24pt initial subtitle size
const LANGS = ["zh", "en", "ko", "off"]; // button cycle: OFF -> ZH -> EN -> KO
const LANG_LABEL = { en: "EN", zh: "ZH", ko: "KO", off: "OFF" };
const SIZE_CHOICES = [12, 16, 20, 24, 32, 40, 48, 56, 64];
const FONT_CHOICES = [
  { label: "默认（跟随）", family: "" },
  { label: "雅黑", family: '"Microsoft YaHei", sans-serif' },
  { label: "等线", family: '"DengXian", sans-serif' },
  { label: "黑体", family: '"SimHei", sans-serif' },
  { label: "宋体", family: '"SimSun", serif' },
  { label: "楷体", family: '"KaiTi", serif' },
  { label: "幼圆", family: '"YouYuan", sans-serif' },
  { label: "隶书", family: '"LiSu", serif' },
  { label: "行楷", family: '"STXingkai", serif' },
  { label: "Arial", family: "Arial, sans-serif" },
  { label: "Georgia", family: "Georgia, serif" },
  { label: "Consolas", family: "Consolas, monospace" },
];

// Subtitle text-style tiers (settings-menu radios; --text-* CLI options).
// The base template in makeCss() carries the startup values; menu clicks
// override them with insertCSS'd !important rules (later rules win).
//  - shadow: drop-shadow tiers ("off" = none, "std" = the classic strong
//    drop shadow, "heavy" = an even heavier shadow for bright video).
//  - stroke (描边) / ink (重墨): bilibili-style OUTSIDE text outlines.
//    NOT -webkit-text-stroke: that property strokes the glyph's CENTERLINE,
//    so half the width paints OVER the letter interior (small font sizes end
//    up with blacked-out strokes). bilibili's DOM renderer instead fakes the
//    outline with offset, blur-free black text-shadow layers (8 directions
//    at 1px for the standard fontborder=1 outline; the 重墨 fontborder=0
//    mode adds larger offsets + a slight blur for a thick, rounded, heavy
//    outside rim -- the CSS equivalent of Canvas strokeText + round lineJoin
//    painted BEFORE the fill). Shadows always sit OUTSIDE the glyph, so the
//    letter interior stays clean at any size.
//  Stroke/ink are two thicknesses of the same outside-rim effect and stay
//  mutually exclusive; the drop-shadow tier composes WITH the rim because
//  both live in the same text-shadow property (textShadowList() below merges
//  outline layers + drop layers into one list).
//  - opacity: subtitle text opacity in percent (the backdrop is separate).
const TEXT_SHADOW_CHOICES = [
  { key: "off", label: "无", css: "" },
  { key: "std", label: "标准", css: "0 0 2px #000,0 0 1px #000,1.5px 1.5px 0.8px #000" },
  { key: "heavy", label: "增强", css: "0 0 5px #000,0 0 2.5px #000,2px 2px 1px #000" },
];
// 细描边: 8 directions at 0.15pt, no blur -- a hairline outside rim that
// scales with the system DPI (pt units, like the font size/letter).
const TEXT_STROKE_CHOICES = [
  { key: "off", label: "无", css: "" },
  {
    key: "thin", label: "细描边",
    css: "0.15pt 0 0 #000,-0.15pt 0 0 #000,0 0.15pt 0 #000,0 -0.15pt 0 #000," +
      "0.15pt 0.15pt 0 #000,-0.15pt -0.15pt 0 #000,0.15pt -0.15pt 0 #000,-0.15pt 0.15pt 0 #000",
  },
];
// 重墨: 8 directions at 0.4pt plus a 0.2pt blur-fill layer for rounded
// corners (a clean bilibili-style outside rim -- no pixel steps).
const TEXT_INK_CHOICES = [
  { key: "off", label: "无", css: "" },
  {
    key: "on", label: "重墨",
    css: "0.4pt 0 0 #000,-0.4pt 0 0 #000,0 0.4pt 0 #000,0 -0.4pt 0 #000," +
      "0.4pt 0.4pt 0 #000,-0.4pt -0.4pt 0 #000,0.4pt -0.4pt 0 #000,-0.4pt 0.4pt 0 #000," +
      "0 0 0.2pt #000",
  },
];
const TEXT_OPACITY_CHOICES = [100, 90, 80, 70, 60]; // percent
// Compose the FULL text-shadow list for a given style state: an outside-rim
// tier (重墨 -> 描边 -> none; two thicknesses, mutually exclusive) followed
// by the drop-shadow tier. Shared by makeCss() (startup opts) and the menu
// apply functions (current globals), so both entry points render identically.
function textShadowList(shadowKey, strokeKey, inkKey) {
  const outline = inkKey === "on" ? TEXT_INK_CHOICES[1].css
    : strokeKey === "thin" ? TEXT_STROKE_CHOICES[1].css : "";
  const shadow = (TEXT_SHADOW_CHOICES.find((c) => c.key === shadowKey) || TEXT_SHADOW_CHOICES[1]).css;
  return [outline, shadow].filter(Boolean).join(",");
}

// Button band geometry (top-left of the window). Same numbers in CSS.
const BTN_X0 = 8;   // first button left
const BTN_Y0 = 8;   // top
const BTN_S = 26;   // button size
const BTN_GAP = 6;  // gap between buttons
const BAND_PAD = 6; // pass-through keep-clickable band padding
// Top-right window buttons: minimize (MDL2 E921) and close (E8BB), anchored to the right.
const RBX = 8;      // right-most button's right offset
const RBTN_W = BTN_S * 2 + BTN_GAP; // total width of the two right buttons

// Display mode: "both" = source + translation (bilingual stacked rows),
// "tr" = translation only (the source flow, incl. its in-progress draft, is
// hidden; the translation flow keeps showing).
const MODES = ["both", "tr"];
const MODE_LABEL = { both: "双语", tr: "译文" };

// Recognition model (settings panel). Mirrors asr_engine.MANUAL_MODELS: the
// four everyday models stay as pills, v3/omni live in the "more" dropdown.
// "auto" is not a model -- it re-enables whisper-tiny LID + SenseVoice
// confirmation + model switching.
const LID_PILLS = [
  ["pz", "Paraformer"], ["rz", "ReazonSpeech"], ["v2", "Parakeet v2"], ["sv", "SenseVoice"],
];
const LID_MORE = [
  ["v3", "Parakeet v3（欧洲 24 语）"], ["omni", "Omnilingual（1600+ 语种）"],
];
const LID_MODEL_LABEL = Object.fromEntries([...LID_PILLS, ...LID_MORE]);
const LID_MODEL_HINT = {
  pz: "中英 · 专用模型（CT 标点补全）",
  rz: "日英 · 专用模型",
  v2: "英语专用",
  sv: "中/日/英/韩/粤 · 内置标点，按自身判定",
  v3: "欧洲 24 语 · 自动判定",
  omni: "1600+ 语种 · 自动判定",
};

// Legacy lid_mode.json written by the language-based panel: migrate the saved
// language to the model it used to route to. en follows the engine's default
// (--en-tier v2); the 24 Parakeet-v3 EU codes all meant the same model.
const LEGACY_LANG_MODEL = Object.assign(
  { zh: "pz", ja: "rz", en: "v2", ko: "sv", yue: "sv", omni: "omni" },
  Object.fromEntries([
    "fr", "de", "es", "ru", "it", "pt", "nl", "pl", "cs", "sk", "sl", "hr",
    "bg", "ro", "hu", "el", "da", "sv", "fi", "et", "lv", "lt", "uk", "mt",
  ].map((code) => [code, "v3"])));

function lidModelLabel(model) {
  return LID_MODEL_LABEL[model] || model;
}

// Subtitle-card background opacity levels (percent; 0 = fully transparent,
// 30 = a soft black card). Text itself is ALWAYS opaque; only the backdrop
// behind the source+translation block changes. The top-center slider is
// 1%-stepped (0..60, fine tuning incl. the 1% startup default); the settings
// menu keeps BG_MENU_CHOICES -- a few common tiers instead of 61 radios.
const BG_CHOICES = Array.from({ length: 13 }, (_, i) => i * 5); // 5..60 step 5
const BG_MENU_CHOICES = [0, 1, ...BG_CHOICES.filter((v) => v !== 0)]; // menu tiers
// Backdrop slider + width slider, side by side, anchored to the LEFT edge of
// the top strip (NOT centered): centering made the sliders drift when the
// window width changed mid-drag (the thumb slid under the cursor -> jitter).
// Fixed left anchor keeps their screen position stable while dragging.
const SLIDER_X0 = 240;              // first slider's left (after the 7-button band)
const SLIDER_W = 120;               // single slider width
const SLIDER_GAP = 20;              // gap between the two sliders
// Window-width slider range (px). Native edge-drag resize is unavailable on
// this transparent/frameless window (no resize handles), so the width slider
// drives setBounds() instead. 560 keeps a readable minimum AND keeps the
// 7-button band + two sliders clear of the top-right window controls; 1200
// covers wide monitors (the creation default stays 900).
const WIN_W_MIN = 560;
const WIN_W_MAX = 1200;
// Defensive window-height floor (auto-fit may report any value, but the
// window must never collapse below the top strip). The card chrome alone
// (6px top inset + 36px padding-top + 8px bottom padding + 12px shadow
// margin) is 62px, so a smaller window could not even show the frame; in
// normal rendering a one-line card measures ~90px, so this floor never
// actually clips content -- it only guards extreme failures.
const MIN_WIN_H = 66;
// (setShape was tried to clip mouse events below the card, but with
// resizable:true the window truly fits the content, so the area below the
// card is OUTSIDE the window -- no clipping needed; setShape only killed
// the native resize border, so it was removed entirely.)

// Button row (left to right): lock | pin | settings | lang | API | mode |
// audio. The pin button (right of the lock) toggles the title-bar auto-hide:
// armed = the bar fades away when the cursor leaves the window, pinned = the
// bar stays. The pass-through keep-clickable band counts 7 buttons now.
const NBUTTONS = 7;

// Audio capture source, fixed at server STARTUP (realtime_transcribe.py picks
// the backend from --input), so switching it RESTARTS the server:
// kill -> respawn with the new --input -> the page's EventSource reconnects
// on its own. speaker/mix need Windows WASAPI loopback (pip install
// soundcard); mic works everywhere.
const INPUTS = ["mic", "speaker", "mix"];
// Icon glyphs: the WHOLE set is Segoe MDL2 Assets (see the CSS rule), a
// MONOCHROME Windows icon font, so every glyph follows the CSS `color` --
// that is what lets the entire row be tinted alike (white, grey, peach...).
//   E72E locked / E785 unlocked / E713 settings gear / E720 mic /
//   E767 speaker / E72C refresh (server restart in flight) /
//   E718 pin armed / E840 pinned / E921 minimize / E8BB close
// Mix is TWO glyphs side by side: MDL2 advances are exactly 1em, so under
// the `.two` 12px rule they measure 24px -- still inside the 26px box
// (at 16px they would be 32px and overflow, hence the smaller rule).
const ICON = {
  mic: "\uE720", speaker: "\uE767", mix: "\uE720\uE767",
  locked: "\uE72E", unlocked: "\uE785", gear: "\uE713", busy: "\uE72C",
  pin: "\uE718", pinned: "\uE840",
  min: "\uE921", close: "\uE8BB",
};
const INPUT_NAME = { mic: "麦克风", speaker: "系统音频", mix: "麦克风+系统音频" };
// Audio button tooltip. The 当前 part must follow audioInput, so
// setPageButtons() re-applies it after every switch (the injected title was
// a one-time snapshot -- the "still says 麦克风 after switching" bug).
function inputTitle() {
  if (serverBusy) return "正在重启识别引擎…";
  return "切换音频源 (当前: " + (INPUT_NAME[audioInput] || "麦克风") + "): 麦克风 → 🔊系统音频 → 🔊两者，引擎重启约15-30秒";
}
// Title-bar auto-hide button. Tooltip follows the CURRENT state, so
// setPageButtons() re-applies it after every toggle (same pattern as
// inputTitle()).
function pinTitle() {
  return barAutohide
    ? "标题栏自动隐藏（点击常驻，不再自动隐藏）"
    : "标题栏已常驻（点击恢复自动隐藏）";
}

// True when a usable openai_translate.json exists next to this app (i.e. the
// server can serve `api:` targets). Keeps the API button inert otherwise.
let apiAvailable = false;
// False = language button sends plain specs (local MT: zh/en/ko).
// True  = language button sends api: specs (OpenAI-compatible endpoint).
let apiMode = false; // set true in whenReady when a config exists (matches the .bat default)

// The server reads the same file (scripts/../openai_translate.json), so this
// detection mirrors translate_api.load_config()'s usability rule: base_url
// and model must be non-empty for the api: route to be usable.
function detectApiConfig() {
  try {
    // Resolve openai_translate.json from the project root next to the exe
    // (the server reads scripts/../openai_translate.json too). __dirname is
    // inside app.asar when packaged, so walk up from the launch dir instead.
    const root = findProjectRoot(launchDir());
    if (!root) return false;
    const p = path.join(root, "openai_translate.json");
    const cfg = JSON.parse(fs.readFileSync(p, "utf8"));
    return !!(cfg && String(cfg.base_url || "").trim() && String(cfg.model || "").trim());
  } catch (_) {
    return false;
  }
}

// Raw connection fields for the settings panel's 翻译 API form (URL / model
// / key only -- everything else in the file is preserved on save).
function readApiConfig() {
  const empty = { base_url: "", model: "", api_key: "", thinking: "default", context_on: true };
  try {
    const root = findProjectRoot(launchDir());
    if (!root) return empty;
    const cfg = JSON.parse(fs.readFileSync(path.join(root, "openai_translate.json"), "utf8"));
    if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) return empty;
    // enable_thinking is tri-state: the KEY ABSENT means "don't send the
    // field at all" (strict MT endpoints like Hunyuan-MT reject it with
    // HTTP 400 20015); present true/false = explicitly on/off.
    const th = cfg.enable_thinking;
    let thinking = "default";
    if (th !== undefined && th !== null) {
      thinking = (th === true || th === "true" || th === 1 || th === "1") ? "on" : "off";
    }
    const cn = Number(cfg.context_n);
    return {
      base_url: String(cfg.base_url || ""),
      model: String(cfg.model || ""),
      api_key: String(cfg.api_key || ""),
      thinking,
      context_on: Number.isFinite(cn) ? cn > 0 : true, // load_config default 6 = on
    };
  } catch (_) {
    return empty;
  }
}

function parseArgs() {
  const args = process.argv.slice(1);
  const opts = {
    url: "http://localhost:8833/",
    width: 900,
    height: 130,
    show: "both",
    mode: "both", // "both" = bilingual rows, "tr" = translation only
    passthrough: false,
    bold: true, // bold subtitle text (startup default ON)
    bg: 1, // subtitle-card backdrop opacity in percent (1% = a barely-there card)
    textShadow: "std", // text-shadow tier: off / std / heavy (default std)
    textStroke: "off", // thin outline (描边): off / thin (off; 重墨 wins when both on)
    textInk: "on", // bilibili-style thick outline (重墨): off / on (default ON)
    textOpacity: 100, // subtitle text opacity in percent
    size: DEFAULT_SIZE,
    font: "",
    lang: "off", // default: no translation (button cycles EN/ZH/KO/OFF)
    input: "mic", // capture source for the autostarted server (--input to override)
    serveArgs: "", // autostart server args (--serve-args to override); empty = no translation
    barAutohide: true, // title bar fades away when the cursor leaves the window
  };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--width") opts.width = parseInt(args[++i], 10) || opts.width;
    else if (a === "--height") opts.height = parseInt(args[++i], 10) || opts.height;
    else if (a === "--size") opts.size = parseInt(args[++i], 10) || DEFAULT_SIZE;
    else if (a === "--font") opts.font = args[++i] || "";
    else if (a === "--lang") opts.lang = args[++i] || "zh";
    else if (a === "--mode") {
      const v = args[++i] || "both";
      opts.mode = MODES.includes(v) ? v : "both";
    } else if (a === "--bold") opts.bold = true;
    else if (a === "--bg") {
      const v = parseInt(args[++i], 10);
      opts.bg = Number.isInteger(v) && v >= 0 && v <= 60 ? v : 1;
    } else if (a === "--text-shadow") {
      const v = args[++i] || "std";
      opts.textShadow = TEXT_SHADOW_CHOICES.some((c) => c.key === v) ? v : "std";
    } else if (a === "--text-stroke") {
      opts.textStroke = (args[++i] || "off") === "thin" ? "thin" : "off";
    } else if (a === "--text-ink") {
      opts.textInk = (args[++i] || "on") === "on" ? "on" : "off";
    } else if (a === "--text-opacity") {
      const v = parseInt(args[++i], 10);
      opts.textOpacity = TEXT_OPACITY_CHOICES.includes(v) ? v : 100;
    } else if (a === "--show") {
      const v = args[++i] || "both";
      if (["final", "partial", "both"].includes(v)) opts.show = v;
    }     else if (a === "--passthrough") opts.passthrough = true;
    else if (a === "--url") opts.url = args[++i] || opts.url;
    else if (a === "--input") {
      const v = args[++i] || "mic";
      opts.input = INPUTS.includes(v) ? v : "mic";
    } else if (a === "--serve-args") opts.serveArgs = args[++i] || opts.serveArgs;
    else if (a === "--no-bar-autohide") opts.barAutohide = false;
    else if (a === "--bar-autohide") opts.barAutohide = true;
  }
  if (!LANGS.includes(opts.lang)) opts.lang = "zh";
  return opts;
}

// ---------------------------------------------------------------------------
// Server autostart: the packaged exe doubles as the 启动早耳.bat + 停止早耳.bat
// pair. On startup, probe the transcribe server (8833). If it is not up,
// spawn it from the hayamimi project root located by walking up from this
// exe's directory; remember the pid so quitting the subtitle window stops
// exactly the server we spawned (a server that was ALREADY running -- e.g.
// started by 启动早耳.bat or another window instance -- is left alone).
// ---------------------------------------------------------------------------
const { spawn, execFileSync } = require("child_process");
const net = require("net");

let spawnedServerPid = null;
// Set when findProjectRoot() fails: this exe is not sitting inside a complete
// package (no scripts/ + .venv/ above it). The window then shows an explicit
// notice instead of a blank Chromium error page (see showRootNotice()).
let serverRootMissing = false;
let failNotified = false; // one dialog per run for a failed overlay load
let warmPollTimer = null; // set while waiting for a freshly spawned server to bind 8833
let foundRoot = null;     // project root once located (for notice-page footers)

function probeServer(timeoutMs, cb) {
  const sock = net.connect({ host: "127.0.0.1", port: 8833 });
  let done = false;
  const finish = (ok) => {
    if (done) return;
    done = true;
    sock.destroy();
    cb(ok);
  };
  sock.setTimeout(timeoutMs, () => finish(false));
  sock.once("connect", () => finish(true));
  sock.once("error", () => finish(false));
}

// Directory the user actually launched the app from. electron-builder's
// portable stub extracts to %TEMP% and re-points process.execPath at the
// extraction dir, but it exposes the original location in the
// PORTABLE_EXECUTABLE_DIR env var. Dev mode uses electron.exe directly.
function launchDir() {
  if (process.env.PORTABLE_EXECUTABLE_DIR) return process.env.PORTABLE_EXECUTABLE_DIR;
  return path.dirname(process.execPath);
}

// Walk up from `fromDir` looking for the project root (scripts/realtime_transcribe.py
// + .venv). Works for the packaged exe in desktop-subtitle/dist (3 levels up)
// and for the dev checkout (electron.exe under node_modules, ~5 levels up).
function findProjectRoot(fromDir) {
  let dir = fromDir;
  for (let i = 0; i < 10 && dir; i++) {
    if (
      fs.existsSync(path.join(dir, "scripts", "realtime_transcribe.py")) &&
      fs.existsSync(path.join(dir, ".venv", "Scripts", "python.exe"))
    )
      return dir;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
  return null;
}

// Startup sequence. Probe 8833 first: a server that is already running is used
// as-is and the overlay loads immediately. Otherwise spawn one and keep the
// window HIDDEN until the port answers -- the first load of the ASR models
// takes 15-30 s, and the window is created with show:false so nothing (not
// even a warm-up page) appears on screen during that wait; the overlay is
// revealed by loadOverlay() the moment the server is ready. Loading the
// overlay URL before that produced an ERR_CONNECTION_REFUSED dialog plus a
// Chromium error page that never recovered, hence the poll. Failure paths
// still show the window: the root notice when the exe is outside a complete
// package, the not-ready page (and a dialog) on any other spawn failure,
// which keeps polling (a server started by hand meanwhile is picked up
// automatically).
function startUpFlow() {
  // Restore the persisted recognition-model choice BEFORE the first
  // spawn: a manual choice must start the engine in --mode single (no LID,
  // no all-tier preload), not be applied after the models are already up.
  readLidModeFile();
  probeServer(1200, (up) => {
    if (up) {
      diag("server already up; loading the overlay");
      loadOverlay();
      return;
    }
    if (spawnServer(false)) {
      diag("server spawning; window stays hidden until 8833 is up");
      warmPollTimer = setInterval(warmTick, 800);
      return;
    }
    if (serverRootMissing) {
      diag("project root missing -> loading the notice page instead of the overlay");
      win.loadURL(noticePageUrl());
      win.show();
      return;
    }
    diag("server spawn failed -> showing the not-ready page");
    win.show();
    showServerNotice("http://localhost:8833/", "spawn-error", "服务器进程无法启动");
    win.loadURL(statusPageUrl());
    warmPollTimer = setInterval(warmTick, 800);
  });
}

function warmTick() {
  if (!win || win.isDestroyed()) {
    clearInterval(warmPollTimer);
    warmPollTimer = null;
    return;
  }
  probeServer(700, (up) => {
    if (!up || !win || win.isDestroyed()) return;
    if (!warmPollTimer) return; // an earlier probe already resolved
    clearInterval(warmPollTimer);
    warmPollTimer = null;
    diag("server is up; loading the overlay");
    loadOverlay();
  });
}

// The window is created hidden (a cold start must show nothing on screen
// until the server is ready); reveal it now that the overlay is loading. A
// transparent window renders as nothing until the first paint, so the user
// only ever sees the overlay itself appear.
function loadOverlay() {
  win.loadURL(buildUrl(opts.url, opts.show));
  if (!win.isVisible()) win.show();
  // Reflect the engine's REAL mode once its callbacks are registered (a
  // launcher may have forced --mode single via --serve-args, or a server
  // was already running: the panel must show that, not our last click).
  syncLidModeFromServer(8);
}

// Spawn argv for the transcribe server. serveArgs (the user's --serve-args
// string) always loses any --input pair: the exe's own --input / the audio
// button state (audioInput) is the single source of truth. When
// applyButtonState is true (restart triggered by the audio button) the
// current translation button state also wins over the launcher default:
// serveArgs' --translate pair is replaced with translateSpec(lang), omitted
// when the button is OFF.
function serverSpawnArgs(applyButtonState) {
  const raw = String((opts && opts.serveArgs) || "").split(/\s+/).filter(Boolean);
  // A persisted manual recognition choice owns --mode/--model: the panel's
  // state is the source of truth, so any launcher-supplied pin is dropped
  // (same precedence rule as --input).
  const manual = lidMode === "manual" && !!lidModel;
  const kept = [];
  for (let i = 0; i < raw.length; i++) {
    const a = raw[i];
    const spaceForm = a === "--input" || (applyButtonState && a === "--translate")
      || (manual && (a === "--mode" || a === "--model" || a === "--lang"));
    const eqForm = a.startsWith("--input=") || (applyButtonState && a.startsWith("--translate="))
      || (manual && (a.startsWith("--mode=") || a.startsWith("--model=") || a.startsWith("--lang=")));
    if (spaceForm || eqForm) {
      if (spaceForm && raw[i + 1] && !raw[i + 1].startsWith("--")) i++; // eat the value
      continue;
    }
    kept.push(a);
  }
  const args = [path.join("scripts", "realtime_transcribe.py"), "--serve", "8833"];
  if (audioInput !== "mic") args.push("--input", audioInput);
  if (manual) args.push("--mode", "single", "--model", lidModel);
  if (applyButtonState) {
    const spec = translateSpec(lang);
    if (spec) args.push("--translate", spec);
  }
  return args.concat(kept);
}

// The portable package ships a `.venv` built on the developer's machine, so
// its pyvenv.cfg still points at THAT machine's Python. 首次运行.bat re-points
// it, but the exe self-heals the file too (the same four lines), so a user who
// skips the one-time setup -- or just double-clicks the exe right after
// unzipping -- still gets a working server. Runs only when the package carries
// its own interpreter (python\ next to the root); the dev checkout has no such
// folder and is never touched.
function ensurePackagedVenv(root) {
  const bundled = path.join(root, "python", "python.exe");
  if (!fs.existsSync(bundled)) return;
  const cfgPath = path.join(root, ".venv", "pyvenv.cfg");
  try {
    let cur = "";
    try { cur = fs.readFileSync(cfgPath, "utf8"); } catch (_) {}
    const vm = /(?:^|\n)\s*version\s*=\s*(\S+)/.exec(cur);
    const lines = [
      "home = " + path.join(root, "python"),
      "executable = " + bundled,
      "include-system-site-packages = false",
      "version = " + (vm ? vm[1] : "3.12.13"),
    ];
    const norm = (s) => s.replace(/\r\n/g, "\n").trim();
    if (norm(cur) === norm(lines.join("\n"))) return; // already bundled
    fs.writeFileSync(cfgPath, lines.join("\r\n") + "\r\n", "utf8");
    diag("pyvenv.cfg self-healed -> " + path.join(root, "python"));
  } catch (e) {
    diag("pyvenv.cfg self-heal FAILED: " + e.message);
  }
}

function spawnServer(applyButtonState) {
  const root = findProjectRoot(launchDir());
  if (!root) {
    serverRootMissing = true;
    diag("project root not found near exe; skipping server autostart"
         + " | execPath=" + process.execPath + " | launchDir=" + launchDir());
    return false;
  }
  serverRootMissing = false;
  foundRoot = root;
  diag("project root = " + root);
  ensurePackagedVenv(root);
  const py = path.join(root, ".venv", "Scripts", "python.exe");
  const args = serverSpawnArgs(applyButtonState);
  try {
    const outFd = fs.openSync(path.join(app.getPath("temp"), "hayamimi-serve.log"), "a");
    const errFd = fs.openSync(path.join(app.getPath("temp"), "hayamimi-serve.err.log"), "a");
    const child = spawn(py, args, {
      cwd: root,
      windowsHide: true,
      stdio: ["ignore", outFd, errFd],
    });
    spawnedServerPid = child.pid;
    diag("server spawned pid=" + child.pid + " args=" + args.join(" "));
    child.on("exit", (code) => diag("spawned server exited code=" + code));
    return true;
  } catch (e) {
    diag("server spawn FAILED: " + e.message);
    return false;
  }
}

// Stop every transcribe server python -- 停止早耳.bat semantics:
// unconditional, only command lines matching "realtime_transcribe" are
// killed (other pythons, e.g. MCP servers, are left alone). Shared by
// before-quit and the audio-source restart. Synchronous execFileSync: an
// async spawn would still be starting the powershell when Electron exits,
// and the kill never lands (observed).
function serverKillSync() {
  try {
    execFileSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'realtime_transcribe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
      ],
      { windowsHide: true, stdio: "ignore", timeout: 8000 }
    );
    diag("server kill: transcribe servers stopped");
  } catch (e) {
    diag("server kill FAILED: " + e.message);
  }
}

// Switch the capture source: kill the server, wait for the port to free,
// respawn with the new --input (+ current button translation state), then
// replay the translation spec over HTTP once the API answers. The overlay
// page never reloads (the EventSource in it just reconnects); subtitles
// pause while the engine reloads its models (~15-30 s).
function restartServerForInput(next) {
  if (serverBusy) {
    diag("restart: already in progress; ignoring " + next);
    return;
  }
  if (!INPUTS.includes(next) || next === audioInput) return;
  const prev = audioInput;
  diag("restart: audio source " + prev + " -> " + next);
  audioInput = next;
  serverBusy = true;
  setPageButtons();
  serverKillSync();
  const freed = Date.now() + 15000;
  const waitDown = () => probeServer(800, (up) => {
    if (!up) return reviveServer(prev);
    if (Date.now() > freed) {
      diag("restart: port never freed; keeping " + prev);
      audioInput = prev;
      serverBusy = false;
      setPageButtons();
      return;
    }
    setTimeout(waitDown, 500);
  });
  waitDown();
}

function reviveServer(prev) {
  if (!spawnServer(true)) {
    audioInput = prev;
    serverBusy = false;
    setPageButtons();
    return;
  }
  const back = Date.now() + 90000; // int8 300M model + workers can take a while
  const waitUp = () => probeServer(800, (up) => {
    if (up) {
      serverBusy = false;
      setPageButtons();
      diag("restart: server back up; replaying translation state");
      replayTranslation(10);
      syncLidModeFromServer(6); // spawn args already carried the choice; belt only
      return;
    }
    if (Date.now() > back) {
      serverBusy = false;
      setPageButtons();
      diag("restart: server did not come back within 90s");
      return;
    }
    setTimeout(waitUp, 700);
  });
  waitUp();
}

// The server's /api/translate callback is registered while models load, so
// early POSTs fail or answer ok:false -- retry until it acks. The respawn
// already carries --translate; this is the belt confirming button == server.
function replayTranslation(attempts) {
  const spec = translateSpec(lang);
  fetch(`http://localhost:${SERVER_PORT}/api/translate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ langs: spec }),
  })
    .then((r) => r.json())
    .then((j) => {
      if (j && j.ok) { diag("restart: translation replayed (" + (spec || "off") + ")"); return; }
      if (attempts > 1) setTimeout(() => replayTranslation(attempts - 1), 2000);
    })
    .catch(() => {
      if (attempts > 1) setTimeout(() => replayTranslation(attempts - 1), 2000);
      else diag("restart: translation replay FAILED (server unreachable?)");
    });
}

function cycleAudioInput() {
  if (process.platform !== "win32") {
    diag("audio switch needs Windows WASAPI loopback; staying on mic");
    return;
  }
  const i = INPUTS.indexOf(audioInput);
  restartServerForInput(INPUTS[(i + 1) % INPUTS.length]);
}

function buildUrl(base, show) {
  if (!show || show === "both") return base;
  return base + (base.includes("?") ? "&" : "?") + "show=" + show;
}

let win = null;
let opts = null;
let passthrough = false;
let lang = "off"; // default: no translation (button cycles OFF/ZH/EN/KO)
// Recognition model (panel; persisted in the project root's lid_mode.json so
// a manual choice survives restarts). Manual is passed to the engine as
// --mode single --model X at spawn time, which also tells it to skip LID and
// preload only X.
let lidMode = "auto";   // "auto" | "manual"
let lidModel = "";      // manual model (pz/rz/v2/sv/v3/omni)
let lidEnTier = "v2";   // engine's en tier, reported by GET /api/lang-mode
let lidErr = "";        // last apply failure, shown in the panel status line
let audioInput = "mic"; // capture source (mic/speaker/mix); switching restarts the server
let serverBusy = false; // audio-source restart in flight: the audio button shows the hourglass icon, re-entry blocked
let mode = "both";  // "both" = bilingual rows, "tr" = translation only
let bg = 1;         // subtitle-card backdrop opacity in percent (1% startup)
let bold = true;    // bold subtitle text (source + translation flows; default ON)
let textShadow = "std";   // text-shadow tier: off / std / heavy (default std)
let textStroke = "off";   // thin outline (描边): off / thin (off; 重墨 wins when both on)
let textInk = "on";       // thick bilibili outline (重墨): off / on (default ON)
let textOpacity = 100;    // subtitle text opacity in percent
let sizeKey = null;
let fontKey = null;
let pollTimer = null;

// pass-through polling: in click-through mode keep exactly two interactive
// zones -- the top-left button band (5 buttons) and the top-right window
// controls (minimize/close); everything else clicks through.
// Pure geometry helpers (no win/screen deps) so they are unit-testable.
function pointInRect(x, y, x0, y0, x1, y1) {
  return x >= x0 && x <= x1 && y >= y0 && y <= y1;
}
function pointInLeftBand(b, x, y) {
  const x2 = b.x + BTN_X0 + BTN_S * NBUTTONS + BTN_GAP * (NBUTTONS - 1) + BAND_PAD;
  const y2 = b.y + BTN_Y0 + BTN_S + BAND_PAD;
  return pointInRect(x, y, b.x, b.y, x2, y2);
}
function pointInRightBand(b, x, y) {
  const x0 = b.x + b.width - RBX - RBTN_W - BAND_PAD;
  const x1 = b.x + b.width;
  const y2 = b.y + BTN_Y0 + BTN_S + BAND_PAD;
  return pointInRect(x, y, x0, b.y, x1, y2);
}
function pointInTopCenterBand(b, x, y) {
  // two side-by-side sliders (bg + width) anchored at the LEFT of the top
  // strip: fixed x span (does not depend on the window width, so the
  // keep-clickable zone matches the sliders exactly, even mid-drag)
  const x0 = b.x + SLIDER_X0 - BAND_PAD;
  const x1 = b.x + SLIDER_X0 + SLIDER_W * 2 + SLIDER_GAP + BAND_PAD;
  const y2 = b.y + BTN_Y0 + BTN_S + BAND_PAD;
  return pointInRect(x, y, x0, b.y, x1, y2);
}
function isCursorInButtonBand() {
  if (!win || win.isDestroyed()) return false;
  const c = screen.getCursorScreenPoint();
  if (pointInLeftBand(win.getBounds(), c.x, c.y)) return true;
  if (pointInRightBand(win.getBounds(), c.x, c.y)) return true;
  if (pointInTopCenterBand(win.getBounds(), c.x, c.y)) return true;
  return false;
}

// --- title-bar auto-hide --------------------------------------------------
// The bar fades out once the cursor leaves the window. To leave the subtitle
// text perfectly still, the window is ALSO shifted down by exactly the amount
// its content collapses (BAR_H == the card's 36px -> 6px top padding), so the
// card's top edge moves down while its bottom edge and the text stay put.
const BAR_H = 30;               // px collapsed/restored per toggle
const BAR_HIDE_DELAY_MS = 350;  // grace period, no flicker when grazing an edge
let barAutohide = true;         // false via --no-bar-autohide or the pin button
let barVisible = true;          // last value pushed to the renderer
let outsideSince = 0;           // timestamp the cursor left (0 = currently inside)

function isCursorInWindow() {
  if (!win || win.isDestroyed()) return false;
  const c = screen.getCursorScreenPoint();
  const b = win.getBounds();
  return pointInRect(c.x, c.y, b.x, b.y, b.x + b.width, b.y + b.height);
}

function refreshBarVisibility() {
  if (!win || win.isDestroyed()) return;
  const inside = isCursorInWindow();
  if (inside || !barAutohide) outsideSince = 0;
  else if (!outsideSince) outsideSince = Date.now();
  let visible = true;
  if (barAutohide && !inside) visible = (Date.now() - outsideSince) < BAR_HIDE_DELAY_MS;
  if (visible === barVisible) return;
  barVisible = visible;
  const b = win.getBounds();
  const dy = BAR_H;
  win.setBounds({
    x: b.x,
    y: visible ? b.y - dy : b.y + dy,
    width: b.width,
    height: visible ? b.height + dy : b.height - dy,
  });
  win.webContents.send("hmy:bar", visible);
  diag("bar " + (visible ? "shown" : "hidden"));
}

function refreshMouseMode() {
  if (!win || win.isDestroyed()) return;
  refreshBarVisibility();
  if (!passthrough) win.setIgnoreMouseEvents(false);
  // in pass-through mode the button band stays clickable ONLY while the bar
  // is visible: a hidden bar must never swallow clicks for invisible buttons
  else win.setIgnoreMouseEvents(!(barVisible && isCursorInButtonBand()), { forward: true });
}

function makeCss() {
  const fontCss = opts.font ? `#box{font-family:${opts.font}!important;}` : "";
  const lx0 = BTN_X0;
  const lx1 = BTN_X0 + BTN_S + BTN_GAP;
  const lx2 = BTN_X0 + (BTN_S + BTN_GAP) * 2;
  const lx3 = BTN_X0 + (BTN_S + BTN_GAP) * 3;
  const lx4 = BTN_X0 + (BTN_S + BTN_GAP) * 4;
  const lx5 = BTN_X0 + (BTN_S + BTN_GAP) * 5;
  const lx6 = BTN_X0 + (BTN_S + BTN_GAP) * 6;
  // subtitle-card backdrop: the bg slider only changes the card FILL. The
  // frame (1px border + soft shadow) and the window insets that make that
  // shadow VISIBLE live in the base #box rule below and in applyBg(): an
  // outside shadow is clipped the moment the card touches the window edge
  // (that is what killed the earlier attempt, which was an OS window shadow
  // -- impossible on a transparent frameless window).
  // NOTE: this variable is currently unused (applyBg() applies the card at
  // runtime); kept in sync so a future re-interpolation cannot resurrect the
  // old padding.
  const cardCss = opts.bg > 0
    ? `#box{background:rgba(0,0,0,${(opts.bg / 100).toFixed(2)})!important;
         padding:36px 8px 8px!important;}
       #hmy-txt-flow,#hmy-tr-flow{margin:0;padding:0;}`
    : "";
  // Parameterized text style -- startup values here; the settings menu
  // overrides each dimension later via insertCSS'd !important rules (later
  // rules win). textShadowList() merges the outside-rim tier (描边/重墨) with
  // the drop-shadow tier into one text-shadow list (both live in the same
  // property and compose). Opacity (percent) applies to both flows (the
  // in-progress draft keeps its own extra 0.9 on top, so its final opacity
  // is 0.9 * textOpacity).
  const textShadow = textShadowList(opts.textShadow, opts.textStroke, opts.textInk);
  const opacityCss = opts.textOpacity < 100 ? `opacity:${(opts.textOpacity / 100).toFixed(2)};` : "";
  return `
    /* ACCUMULATING subtitle flow (desktop window owns ALL rendering):
       confirmed finals flow left-to-right / wrap on lines (top-anchored),
       and the in-progress partial draft rides INLINE at the end of the same
       text line -- exactly the current #final-line + #partial-line "same
       line" look. Each confirmed final segment keeps its own 8s lifetime and
       is removed when it expires. The same happens for translations below.
       The server's native overlay script still writes #final-line /
       #partial-line -- hide them; we render our own flow. */
    /* card frame: 1px outline + soft OUTSIDE shadow, always present (the bg
       slider only changes the FILL). The card starts ABOVE the button row,
       so the frame encloses the whole title bar; the ghost buttons have no
       visible box, so the icons keep their visual padding inside the frame.
       The 6px insets are what make the outside shadow visible at all -- it
       is clipped the moment the card touches the window edge (that is what
       killed the earlier attempt, which was an OS window shadow: impossible
       on a transparent frameless window; a CSS box-shadow works, it just
       needs this room, plus the bottom margin in fitHeight()).
       The padding pair keeps the TEXT pixel-identical to the old layout
       where the card sat below the band (6+36 == 2+40, 6+8 == 0+14). */
    #box{top:6px!important;bottom:auto!important;left:6px!important;
         right:6px!important;text-align:left!important;
         max-width:100%;white-space:normal;box-sizing:border-box;
         padding:36px 8px 8px!important;border-radius:14px!important;
         border:1px solid rgba(255,255,255,0.22)!important;
         box-shadow:0 0 7px rgba(0,0,0,0.55)!important;}
    #final-line,#partial-line{display:none!important;}
    /* translation-only mode: hide the whole source flow (confirmed segments
       and its in-progress draft); only the translation flow stays visible */
    html.hmy-tr-only #hmy-txt-flow{display:none!important;}
    /* source-text flow: confirmed segments + trailing in-progress draft */
    #hmy-txt-flow{display:block;width:100%;box-sizing:border-box;
         font-size:${opts.size}px!important;color:#fff;
         ${textShadow ? "text-shadow:" + textShadow + ";" : ""}
         ${opacityCss}
         white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;
         line-height:1.35;}
    .hmy-txt-seg{display:inline;}
    .hmy-txt-part{display:inline;font-style:italic;opacity:0.9;}
    /* translation flow: confirmed per-segment translations + trailing draft */
    #hmy-tr-flow{display:block;width:100%;box-sizing:border-box;
         font-size:${opts.size}px!important;color:#ffd75e;
         ${textShadow ? "text-shadow:" + textShadow + ";" : ""}
         ${opacityCss}
         white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;
         line-height:1.25;}
    .hmy-tr-seg{display:inline;}
    .hmy-tr-draft{display:inline;font-style:italic;}
    /* (fade-out removed: segments disappear immediately at lifetime end) */
    #box{font-size:${opts.size}px!important;}
    ${fontCss}
    /* OS-native drag while interactive: only the card and the top strip are
       draggable. body itself is no-drag, so the window's bottom margin (any
       area below the card) never drags. The card region drags; a fixed
       ::before strip covers the button row (which sits above the card when
       bg=0) and stays draggable there. Buttons/slider keep their own
       no-drag + top z-index, so they stay clickable inside both regions. */
    html.hmy-drag-mode body{position:fixed;top:0;right:0;bottom:0;left:0;
                            -webkit-app-region:no-drag;}
    html.hmy-drag-mode body::before{content:"";position:fixed;top:0;left:0;
                            right:0;height:42px;-webkit-app-region:drag;}
    html.hmy-drag-mode #box{-webkit-app-region:drag;}
    /* (no cursor:move anywhere: the drag regions stay visually quiet -- the
       default arrow shows everywhere, and buttons keep their pointer) */
    /* buttons: no-drag => clickable even inside the drag region.
       Ghost look: no fill/border at rest, one flat accent colour for the
       glyph (icons and labels alike), no outline/shadow; hovering reveals
       the translucent pill + border. */
    .hmy-btn{position:fixed;top:${BTN_Y0}px;width:${BTN_S}px;height:${BTN_S}px;
             display:flex;align-items:center;justify-content:center;
             font-size:13px;line-height:1;background:transparent;
color:#d68c27; /* same opencode theme family, LIGHT-mode orange: a step
                darker/amber than the dark-mode peach #fab283, so it holds
                up on white backdrops too (and still pops on dark video).
                The icons are monochrome MDL2 glyphs, so they
                inherit this just like the text labels do. NO outline/shadow
                at all: any rim/glow only muddied the edges (a blurred glow
                read as "out of focus", a dark rim ghosted thin strokes). */
             border:1px solid transparent;border-radius:8px;
             cursor:pointer!important;user-select:none;
             -webkit-app-region:no-drag;z-index:2147483647;
             font-family:'Segoe UI',sans-serif;
             transition:background .15s ease,border-color .15s ease,
                        transform .08s ease,box-shadow .15s ease,
                        opacity .18s ease;}
    /* icon buttons: Segoe MDL2 Assets at 16px -- a monochrome Windows icon
       font, so every glyph follows the CSS color property (that is how the
       whole row is tinted alike). MDL2 ships ONE weight and its gear/mic are
       thin-line glyphs, so -webkit-text-stroke in currentColor adds body
       without changing the hue (the mix pair takes a lighter 0.2px, see the
       .two rule below). */
    #hmy-lock-btn,#hmy-pin-btn,#hmy-menu-btn,#hmy-audio-btn{
        font-family:'Segoe MDL2 Assets',sans-serif;font-size:16px;
        -webkit-text-stroke:0.35px currentColor;}
    /* mix = TWO glyphs: they only fit the 26px box at 12px, and they take a
       LIGHTER stroke (0.2px) so two bold glyphs side by side stay readable */
    #hmy-audio-btn.two{font-size:12px;-webkit-text-stroke:0.2px currentColor;}
    /* window controls keep their Segoe MDL2 text glyphs (E921 bar / E8BB
       cross -- monochrome, so they follow the colour too); 12px because
       those glyphs are full-bleed and read a size larger. */
    #hmy-min-btn,#hmy-close-btn{
        font-family:'Segoe MDL2 Assets',sans-serif;font-size:12px;
        -webkit-text-stroke:0.35px currentColor;}
    #hmy-lock-btn{left:${lx0}px;}
    #hmy-pin-btn{left:${lx1}px;}
    #hmy-menu-btn{left:${lx2}px;}
    /* text labels: 12px is the largest that keeps 双语 inside the 26px box */
    #hmy-lang-btn{left:${lx3}px;font-family:'Segoe UI',sans-serif;font-weight:700;
                  font-size:12px;}
    #hmy-api-btn{left:${lx4}px;font-family:'Segoe UI',sans-serif;font-weight:700;
                 font-size:12px;}
    #hmy-mode-btn{left:${lx5}px;font-family:'Segoe UI',sans-serif;font-weight:700;
                  font-size:12px;}
    #hmy-audio-btn{left:${lx6}px;}
    /* top-right window controls: minimize (E921) and close (E8BB) */
    #hmy-min-btn{right:${RBX + BTN_S + BTN_GAP}px;}
    #hmy-close-btn{right:${RBX}px;}
    #hmy-close-btn:hover{background:rgba(220,60,60,0.85);border-color:rgba(255,120,120,0.6);}
    /* hover reveals the translucent pill (the ghost buttons' affordance) */
    .hmy-btn:hover{background:rgba(65,65,75,0.9);border-color:rgba(255,255,255,0.6);
                   box-shadow:0 2px 8px rgba(0,0,0,0.45);}
    .hmy-btn:active{transform:translateY(1px);}
    .hmy-btn *{pointer-events:none;}
    /* top-center sliders (backdrop opacity + window width): thin track,
       round thumb, centered side by side on the window's top strip */
    #hmy-bg-slider{position:fixed;top:${BTN_Y0 + (BTN_S - 10) / 2}px;
         left:${SLIDER_X0}px;width:${SLIDER_W}px;
         height:10px;-webkit-appearance:none;appearance:none;background:transparent;
         cursor:pointer!important;user-select:none;-webkit-app-region:no-drag;
         transition:opacity .18s ease;
         z-index:2147483647;outline:none;}
    #hmy-w-slider{position:fixed;top:${BTN_Y0 + (BTN_S - 10) / 2}px;
         left:${SLIDER_X0 + SLIDER_W + SLIDER_GAP}px;width:${SLIDER_W}px;
         height:10px;-webkit-appearance:none;appearance:none;background:transparent;
         cursor:pointer!important;user-select:none;-webkit-app-region:no-drag;
         transition:opacity .18s ease;
         z-index:2147483647;outline:none;}
    #hmy-bg-slider::-webkit-slider-runnable-track,#hmy-w-slider::-webkit-slider-runnable-track{height:4px;border-radius:2px;
         background:rgba(255,255,255,0.15);transition:background .15s ease;}
    #hmy-bg-slider::-webkit-slider-thumb,#hmy-w-slider::-webkit-slider-thumb{-webkit-appearance:none;width:10px;height:10px;
         border-radius:50%;background:rgba(255,255,255,0.85);margin-top:-3px;
         border:1px solid rgba(255,255,255,0.30);
         box-shadow:0 1px 3px rgba(0,0,0,0.8),0 0 2px rgba(0,0,0,0.9);
         transition:transform .08s ease,box-shadow .15s ease;}
    #hmy-bg-slider:hover::-webkit-slider-runnable-track,#hmy-w-slider:hover::-webkit-slider-runnable-track{background:rgba(255,255,255,0.28);}
    #hmy-bg-slider:hover::-webkit-slider-thumb,#hmy-w-slider:hover::-webkit-slider-thumb{background:rgba(255,255,255,0.85);}
    #hmy-bg-slider:active::-webkit-slider-thumb,#hmy-w-slider:active::-webkit-slider-thumb{transform:scale(1.15);}
    /* API channel unavailable (no openai_translate.json): dimmed, inert */
    .hmy-btn.off{opacity:0.45;background:transparent;
                 border-color:transparent;cursor:default!important;}
    /* --- title-bar auto-hide (decided by the main process, see
       refreshBarVisibility()) -------------------------------------------
       The bar FADES OUT and its row COLLAPSES: #box loses exactly the
       36px -> 6px top padding, so the window really becomes one row shorter.
       The main process shifts the window down by the same 30px at the same
       time, which keeps the subtitle text and the card's bottom edge exactly
       where they were (no jump when the pointer crosses the window). */
    html.hmy-bar-off .hmy-btn,
    html.hmy-bar-off #hmy-bg-slider,
    html.hmy-bar-off #hmy-w-slider{opacity:0;pointer-events:none;}
    html.hmy-bar-off #box{padding-top:6px!important;}
  `;
}

function makeInitJs() {
  return `
(function(){
  try {
    if (window.__hmyInit) { console.log('hmy: already init'); return; }
    window.__hmyInit = true;
    window.__hmyLang = ${JSON.stringify(opts.lang)};
    window.__hmyTr = {};
    window.__hmyMode = 'both'; // 'both' = bilingual rows, 'tr' = translation only

    function mkBtn(id, text, title){
      var d = document.createElement('div');
      d.className = 'hmy-btn';
      d.id = id;
      d.textContent = text;
      d.title = title;
      return d;
    }
    var lock = mkBtn('hmy-lock-btn', ${JSON.stringify(passthrough ? ICON.locked : ICON.unlocked)}, '点击切换穿透');
    lock.addEventListener('click', function(){
      console.log('hmy: btn-lock-click');
      window.desktopSubtitle.togglePassthrough();
    });
    var pinBtn = mkBtn('hmy-pin-btn', ${JSON.stringify(opts.barAutohide !== false ? ICON.pin : ICON.pinned)}, ${JSON.stringify(pinTitle())});
    pinBtn.addEventListener('click', function(){
      console.log('hmy: btn-pin-click');
      window.desktopSubtitle.togglePin();
    });
    var menuBtn = mkBtn('hmy-menu-btn', ${JSON.stringify(ICON.gear)}, '打开设置菜单');
    menuBtn.addEventListener('click', function(){
      console.log('hmy: btn-menu-click');
      window.desktopSubtitle.showMenu();
    });
    var langBtn = mkBtn('hmy-lang-btn', '${LANG_LABEL[opts.lang] || "EN"}', '切换翻译语言 (EN/ZH/KO/OFF)');
    langBtn.addEventListener('click', function(){
      console.log('hmy: btn-lang-click');
      window.desktopSubtitle.cycleLang();
    });
    var apiBtn = mkBtn('hmy-api-btn', '${apiAvailable ? '本地' : '—'}', '切换翻译通道: 本地模型 / OpenAI API (需要 openai_translate.json)');
    apiBtn.addEventListener('click', function(){
      console.log('hmy: btn-api-click');
      window.desktopSubtitle.cycleApi();
    });
    var modeBtn = mkBtn('hmy-mode-btn', '双语', '切换显示: 双语(原文+译文) / 仅译文');
    modeBtn.addEventListener('click', function(){
      console.log('hmy: btn-mode-click');
      window.desktopSubtitle.toggleMode();
    });
    var audioBtn = mkBtn('hmy-audio-btn', ${JSON.stringify(ICON[audioInput] || ICON.mic)}, ${JSON.stringify(inputTitle())});
    audioBtn.className = ${JSON.stringify(audioInput === "mix" ? "hmy-btn two" : "hmy-btn")};
    audioBtn.addEventListener('click', function(){
      console.log('hmy: btn-audio-click');
      window.desktopSubtitle.cycleInput();
    });
    var minBtn = mkBtn('hmy-min-btn', ${JSON.stringify(ICON.min)}, '最小化窗口');
    minBtn.addEventListener('click', function(){
      console.log('hmy: btn-min-click');
      window.desktopSubtitle.minimize();
    });
    var closeBtn = mkBtn('hmy-close-btn', ${JSON.stringify(ICON.close)}, '退出字幕窗');
    closeBtn.addEventListener('click', function(){
      console.log('hmy: btn-close-click');
      window.desktopSubtitle.close();
    });
    var bgSlider = document.createElement('input');
    bgSlider.type = 'range';
    bgSlider.id = 'hmy-bg-slider';
    bgSlider.min = '0';
    bgSlider.max = '60';
    bgSlider.step = '1';
    bgSlider.value = '1'; // the startup default; applyBg() re-syncs it after init
    bgSlider.title = '背景透明度调节 (0-60%)';
    // Only a press ON the thumb may start a drag: a native <input type=range>
    // jumps the value when ANYWHERE on the track is pressed, which made these
    // two sliders easy to knock out of place by accident. Pressing the thumb
    // itself is left untouched, so the native drag (with pointer capture, i.e.
    // dragging on past the element's edge) still works exactly as before.
    // Tolerance = thumb radius (5px) + 4px slack.
    function lockTrack(el){
      el.addEventListener('mousedown', function(ev){
        var r = el.getBoundingClientRect();
        var mn = parseFloat(el.min), mx = parseFloat(el.max), v = parseFloat(el.value);
        var thumb = 10; // ::-webkit-slider-thumb width, see the CSS
        var cx = r.left + (v - mn) / (mx - mn) * (r.width - thumb) + thumb / 2;
        if (Math.abs(ev.clientX - cx) > 9) ev.preventDefault();
      });
    }
    lockTrack(bgSlider);
    bgSlider.addEventListener('input', function(){
      console.log('hmy: bg-slider-input ' + bgSlider.value);
      window.desktopSubtitle.setBgAlpha(parseInt(bgSlider.value, 10));
    });
    var wSlider = document.createElement('input');
    wSlider.type = 'range';
    wSlider.id = 'hmy-w-slider';
    wSlider.min = '560';
    wSlider.max = '1200';
    wSlider.step = '10';
    wSlider.value = '900'; // equals the window's creation width
    wSlider.title = '窗口宽度调节 (560-1200px)';
    lockTrack(wSlider);
    wSlider.addEventListener('input', function(){
      console.log('hmy: w-slider-input ' + wSlider.value);
      window.desktopSubtitle.setWidth(parseInt(wSlider.value, 10));
    });
    document.body.appendChild(lock);
    document.body.appendChild(pinBtn);
    document.body.appendChild(menuBtn);
    document.body.appendChild(langBtn);
    document.body.appendChild(apiBtn);
    document.body.appendChild(modeBtn);
    document.body.appendChild(audioBtn);
    document.body.appendChild(minBtn);
    document.body.appendChild(closeBtn);
    document.body.appendChild(bgSlider);
    document.body.appendChild(wSlider);
    console.log('hmy: buttons injected');

    // --- title-bar auto-hide: the main process polls the cursor (40ms) and
    // pushes the wanted state; it also collapses the window by one row after
    // shifting it down by the same amount, so here we only fade + collapse
    // the padding (see html.hmy-bar-off in makeCss).
    if (window.desktopSubtitle.onBarVisible) {
      window.desktopSubtitle.onBarVisible(function(on){
        document.documentElement.classList.toggle('hmy-bar-off', !on);
      });
    }

    // ACCUMULATING subtitle flows (desktop window owns ALL rendering):
    //   #hmy-txt-flow   source text: confirmed segments inline, the current
    //                   partial draft rides INLINE at the end (same-line look
    //                   as the original #final-line + #partial-line pair), so
    //                   a new final APPENDS after older finals and several can
    //                   share the screen.
    //   #hmy-tr-flow    translations: one row per confirmed source segment +
    //                   an in-progress draft at the end.
    // 'segs' maps seq -> {srcEl, trEl} so a late 'translation' (API is
    // slow) can still attach to its segment.
    //
    // Lifetime policy (replaces the old per-segment 8s TTL, which re-flowed
    // the text at random moments while reading): content is.STABLE while
    // anyone is speaking or translating -- the ONLY changes are at a new
    // final's arrival. When no event has been seen for IDLE_CLEAR_MS
    // (silence, paused video, just finished reading), the whole card is
    // cleared once; during very long continuous speech a roll-up cap keeps
    // the card bounded, dropping the OLDEST segments at the same moment a
    // new one appends (scroll-with-reading, never mid-read).
    var IDLE_CLEAR_MS = 2000;  // no SSE event at all for this long -> clear the card
    var MAX_CHARS = 200;       // roll-up cap: this many source chars on screen -> drop oldest on append
    var box = document.getElementById('box');
    var txtFlow = null, trFlow = null, txtPart = null, trDraft = null;
    var segs = {};        // seq -> {srcEl, trEl}
    var lastSeq = 0;
    var idleTimer = null;
    var seqOrder = [];    // confirmed seqs in append order (roll-up eviction)
    if (box) {
      txtFlow = document.createElement('div');
      txtFlow.id = 'hmy-txt-flow';
      box.appendChild(txtFlow);
      txtPart = document.createElement('span');
      txtPart.className = 'hmy-txt-part';
      txtFlow.appendChild(txtPart);
      trFlow = document.createElement('div');
      trFlow.id = 'hmy-tr-flow';
      box.appendChild(trFlow);
      trDraft = document.createElement('span');
      trDraft.className = 'hmy-tr-draft';
      trFlow.appendChild(trDraft);
    }
    function bumpActivity() {
      // every SSE event (finals, drafts, late translations) counts as
      // activity; only TRUE silence -- zero events -- trips the clear
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(function(){
        // idle clear: the one visual change that happens without content.
        // The inline draft spans are rebuilt fresh so the next utterance's
        // drafts render normally.
        for (var k in segs) delete segs[k];
        seqOrder.length = 0;
        if (!txtFlow || !trFlow) return;
        txtFlow.textContent = '';
        trFlow.textContent = '';
        txtPart = document.createElement('span');
        txtPart.className = 'hmy-txt-part';
        txtFlow.appendChild(txtPart);
        trDraft = document.createElement('span');
        trDraft.className = 'hmy-tr-draft';
        trFlow.appendChild(trDraft);
      }, IDLE_CLEAR_MS);
    }
    function dropOldest() {
      // roll-up eviction, called only at append time: while the source flow
      // holds >= MAX_CHARS, remove the oldest confirmed segment (source +
      // translation rows together) -- the card stays bounded during
      // continuous speech, and the reflow coincides with the new sentence
      // appearing (scroll-with-reading, never mid-read)
      while (txtFlow.textContent.trim().length >= MAX_CHARS) {
        var old = seqOrder.shift();
        if (old === undefined) break;
        var sg = segs[old];
        if (!sg) { delete segs[old]; continue; }
        if (sg.srcEl && sg.srcEl.parentNode) sg.srcEl.parentNode.removeChild(sg.srcEl);
        if (sg.trEl && sg.trEl.parentNode) sg.trEl.parentNode.removeChild(sg.trEl);
        delete segs[old];
        if (txtFlow.textContent.trim().length < MAX_CHARS) break;
      }
    }
    window.__hmyShowTr = function(){
      // language switch: refresh only the LATEST confirmed translation from
      // the per-language cache (older segments keep their history); drafts
      // aren't cached, so clear the draft span.
      var txt = window.__hmyTr[window.__hmyLang] || '';
      var sg = segs[lastSeq];
      if (sg && sg.trEl) sg.trEl.textContent = txt;
      if (trDraft) trDraft.textContent = '';
    };

    // --- height auto-fit: every 500ms measure how tall #box (subtitle +
    // translation rows) actually is and ask the window to grow/shrink to fit.
    // Width stays fixed -- Chinese/any text wraps instead of overflowing.
    // Polling only sets the height; it never reacts to the window height, so
    // there is no feedback loop (unlike the removed MutationObserver code).
    function fitHeight() {
      var box = document.getElementById('box');
      if (!box) return;
      // bottom margin 12px: the card's outside shadow extends ~7px below the
      // card (no y-offset, blur 7) and the window edge would clip it -- and
      // that shadow is what keeps the box findable. Well under the old 16px
      // value, which made the transparent strip below the card swallow
      // scroll-wheel events.
      var h = box.offsetTop + box.offsetHeight + 12; // rows + shadow room
      window.desktopSubtitle.resize(0, h);
    }
    setInterval(fitHeight, 500);

    // ACCUMULATING flows: a 'final' APPENDS a new confirmed segment to the
    // source flow (after all older finals -- several can share the screen,
    // each with its own 8s lifetime), and a 'translation' fills that
    // segment's translation row (matched by seq). The in-progress draft
    // rides inline at the end of both flows and is cleared on final, exactly
    // like the original #partial-line next to #final-line.
    var es = new EventSource('/events');
    es.onmessage = function(e){
      bumpActivity();
      var ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }

      if (ev.type === 'partial') {
        // current in-progress source text rides inline at the end of the
        // source flow (same-line look, mirroring #partial-line). After an
        // idle clear the span was rebuilt; after a roll-up drop it may have
        // been detached for a moment -- re-attach before writing.
        if (txtPart && txtPart.parentNode !== txtFlow) txtFlow.appendChild(txtPart);
        if (txtPart) txtPart.textContent = ev.text;
        return;
      }
      if (ev.type === 'partial_translation') {
        // current in-progress draft translation rides inline at the end of
        // the translation flow; never cached in __hmyTr (that map only
        // holds confirmed translations).
        if (trDraft && trDraft.parentNode !== trFlow) trFlow.appendChild(trDraft);
        if (window.__hmyLang === ev.lang && trDraft) {
          trDraft.textContent = ev.text;
        }
        return;
      }
      if (ev.type === 'translation') {
        window.__hmyTr[ev.lang] = ev.text;   // latest per-language translation
        // Attach to the segment by seq if the language is active and the
        // segment is still on screen; otherwise it just stays cached (the
        // language switch handler re-renders the newest segment from cache).
        if (window.__hmyLang === ev.lang) {
          var ty = segs[ev.seq];
          if (ty && ty.trEl) ty.trEl.textContent = ev.text;
          // THE confirmed translation replaces the in-progress draft: clear
          // it now (it survives finals, but not the real translation -- a
          // leftover draft next to the confirmed rows is the old "final 都
          // 出来了 partial 还在" state).
          if (trDraft) trDraft.textContent = '';
        }
        return;
      }
      if (ev.type === 'final') {
        // promote the current draft to a confirmed segment: roll-up the
        // oldest segments if the caps are hit, then append to the source
        // flow after older finals and give it an empty translation row the
        // late 'translation' will fill. NO per-segment lifetime anymore --
        // without this timer the text never reflows while reading.
        var seq = (ev.seq !== undefined && ev.seq !== null) ? ev.seq : (++lastSeq);
        if (seq > lastSeq) lastSeq = seq;
        dropOldest();
        var srcEl = document.createElement('span');
        srcEl.className = 'hmy-txt-seg';
        srcEl.textContent = ev.text;
        srcEl.textContent += ' ';    // visual gap between accumulated segments
        // keep the draft span AFTER this new segment: move it to the end
        if (txtPart && txtPart.parentNode === txtFlow) {
          txtFlow.removeChild(txtPart);
        }
        txtFlow.appendChild(srcEl);
        if (txtPart) { txtFlow.appendChild(txtPart); txtPart.textContent = ''; }

        var trEl = document.createElement('span');
        trEl.className = 'hmy-tr-seg';
        if (trDraft && trDraft.parentNode === trFlow) {
          trFlow.removeChild(trDraft);
        }
        trFlow.appendChild(trEl);
        if (trDraft) { trFlow.appendChild(trDraft); }
        // NOTE: the draft translation is KEPT here -- it must survive until
        // the confirmed 'translation' for this final arrives and replaces it
        // (clearing it now would leave an empty translation row for the whole
        // API/MT round-trip window). The 'translation' handler clears it.

        segs[seq] = { srcEl: srcEl, trEl: trEl };
        seqOrder.push(seq);
        return;
      }
    };
    console.log('hmy: init ok');
  } catch (err) {
    console.log('hmy: init FAILED ' + ((err && err.message) || err));
  }
})();
`;
}

function setPageButtons() {
  if (!win || win.isDestroyed()) return;
  // Icon semantics: the CLOSED padlock = window is LOCKED in place
  // (click-through/passthrough, cannot be dragged) -- the OPEN padlock =
  // unlocked, can drag/move the window. (Previously inverted: passthrough
  // showed the open padlock, which was confusing.)
  const icon = passthrough ? ICON.locked : ICON.unlocked;
  const label = LANG_LABEL[lang] || "EN";
  win.webContents
    .executeJavaScript(
      `(function(){
        var lock=document.getElementById('hmy-lock-btn');
        if(lock){ lock.textContent=${JSON.stringify(icon)}; lock.className='hmy-btn'; }
        var lp=document.getElementById('hmy-lang-btn');
        if(lp){ lp.textContent=${JSON.stringify(label)}; }
        var ap=document.getElementById('hmy-api-btn');
        if(ap){
          var apiLabel = ${JSON.stringify(apiAvailable ? (apiMode ? 'API' : '本地') : '—')};
          ap.textContent = apiLabel;
          ap.className = 'hmy-btn' + (${JSON.stringify(apiAvailable ? '' : ' off')});
        }
        var md=document.getElementById('hmy-mode-btn');
        if(md){ md.textContent = ${JSON.stringify(MODE_LABEL[mode])}; }
        var ab=document.getElementById('hmy-audio-btn');
        if(ab){
          ab.textContent = ${JSON.stringify(serverBusy ? ICON.busy : (ICON[audioInput] || ICON.mic))};
          ab.className = ${JSON.stringify(audioInput === "mix" ? "hmy-btn two" : "hmy-btn")};
          ab.title = ${JSON.stringify(inputTitle())};
        }
        var pb=document.getElementById('hmy-pin-btn');
        if(pb){
          pb.textContent = ${JSON.stringify(barAutohide ? ICON.pin : ICON.pinned)};
          pb.title = ${JSON.stringify(pinTitle())};
        }
      })();`
    )
    .catch(() => {});
}

function applyPassthrough(flag) {
  passthrough = flag;
  refreshMouseMode();
  setPageButtons();
  win.webContents
    .executeJavaScript(
      `(function(){var de=document.documentElement;${flag ? "de.classList.remove('hmy-drag-mode');" : "de.classList.add('hmy-drag-mode');"}})();`
    )
    .catch(() => {});
}

// Extract the server's http port from opts.url (default 8833), so the
// language button can POST the hot-switch to the right endpoint.
const SERVER_PORT = (() => {
  const m = (opts && opts.url || "http://localhost:8833/").match(/:\d+/);
  return m ? parseInt(m[0].slice(1), 10) : 8833;
})();

// Translate target spec sent to the server for the currently selected lang:
// "" for off; "api:<target>" when the API channel is active (and available);
// plain "<target>" (local MT) otherwise.
function translateSpec(lang) {
  if (lang === "off") return "";
  return apiMode && apiAvailable ? "api:" + lang : lang;
}

function notifyServerTranslation(spec) {
  // spec: "" = off, otherwise e.g. "en,zh,ko" (local) or "api:zh" (API).
  // Fire-and-forget: failures just mean the server isn't reachable.
  try {
    fetch(`http://localhost:${SERVER_PORT}/api/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ langs: spec }),
    }).catch(() => {});
  } catch (_) {}
}

// --- Recognition model (manual pinning + model residency) ------------------

const LID_MODE_FILE = "lid_mode.json"; // project-root state, next to openai_translate.json

function readLidModeFile() {
  try {
    const root = findProjectRoot(launchDir());
    if (!root) return;
    const c = JSON.parse(fs.readFileSync(path.join(root, LID_MODE_FILE), "utf8"));
    const saved = c && c.mode === "manual" ? String(c.model || "") : "";
    // legacy language-based file: c.lang was a language code, not a model
    const legacy = c && c.mode === "manual" && c.lang
      ? LEGACY_LANG_MODEL[String(c.lang).toLowerCase()] : "";
    const model = LID_MODEL_LABEL[saved] ? saved : (legacy || "");
    if (model) {
      lidMode = "manual";
      lidModel = model;
    } else {
      lidMode = "auto";
      lidModel = "";
    }
    diag("lid: restored mode=" + lidMode + (lidModel ? " model=" + lidModel : ""));
  } catch (_) { /* absent file: auto */ }
}

function saveLidModeFile() {
  try {
    const root = findProjectRoot(launchDir());
    if (!root) return;
    fs.writeFileSync(path.join(root, LID_MODE_FILE),
      JSON.stringify({ mode: lidMode, model: lidModel }, null, 2) + "\n", "utf8");
  } catch (e) {
    diag("lid: save FAILED " + e.message);
  }
}

// POST the choice; the server acks with the authoritative state. On success
// persist it and resync the panel; on failure keep the old state and show
// lidErr in the panel's status line.
function applyLidMode(value) {
  const manual = !!(value && value !== "auto");
  const model = manual ? String(value) : "";
  lidErr = "";
  try {
    fetch(`http://localhost:${SERVER_PORT}/api/lang-mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(manual ? { mode: "manual", model } : { mode: "auto" }),
    })
      .then((r) => r.json())
      .then((j) => {
        if (!j || !j.ok) throw new Error((j && (j.err || j.error)) || "服务器拒绝切换");
        lidMode = j.mode === "manual" ? "manual" : "auto";
        lidModel = j.model || "";
        if (j.en_tier) lidEnTier = j.en_tier;
        saveLidModeFile();
        diag("lid: mode=" + lidMode + (lidModel ? " model=" + lidModel : "") +
             " resident=" + ((j.resident || []).join(",") || "-"));
        syncPanel();
      })
      .catch((e) => {
        lidErr = String((e && e.message) || e);
        diag("lid: apply FAILED " + lidErr);
        syncPanel();
      });
  } catch (e) {
    lidErr = String((e && e.message) || e);
    diag("lid: apply FAILED " + lidErr);
    syncPanel();
  }
}

// Re-read the engine's real state (e.g. --serve-args forced --mode single,
// or a server that was already running). Retries while the model callbacks
// aren't registered yet (models still loading).
function syncLidModeFromServer(attempts) {
  fetch(`http://localhost:${SERVER_PORT}/api/lang-mode`)
    .then((r) => r.json())
    .then((j) => {
      if (!j || !j.ok) {
        if (attempts > 1) setTimeout(() => syncLidModeFromServer(attempts - 1), 2000);
        return;
      }
      lidMode = j.mode === "manual" ? "manual" : "auto";
      lidModel = j.model || "";
      if (j.en_tier) lidEnTier = j.en_tier;
      diag("lid: server state mode=" + lidMode + (lidModel ? " model=" + lidModel : "") +
           " resident=" + ((j.resident || []).join(",") || "-"));
      syncPanel();
    })
    .catch(() => {
      if (attempts > 1) setTimeout(() => syncLidModeFromServer(attempts - 1), 2000);
    });
}

function cycleLang() {
  const idx = LANGS.indexOf(lang);
  lang = LANGS[(idx + 1) % LANGS.length];
  // The language button now controls BOTH sides:
  //  - the server's translation (hot-switched via POST /api/translate)
  //  - which of those translations this window displays
  notifyServerTranslation(translateSpec(lang));
  setPageButtons();
  win.webContents
    .executeJavaScript(
      `(function(){window.__hmyLang=${JSON.stringify(lang)};if(window.__hmyShowTr)window.__hmyShowTr();})();`
    )
    .catch(() => {});
}

// API/本地 channel toggle. Only meaningful when openai_translate.json exists
// (apiAvailable true); otherwise it stays local and the button is inert.
function cycleApi() {
  applyApiMode(!apiMode);
}

function applyApiMode(on) {
  apiMode = !!(on && apiAvailable);
  notifyServerTranslation(translateSpec(lang)); // re-apply current lang via new channel
  setPageButtons();
}

// Display mode: "both" = source + translation rows (bilingual), "tr" = only
// the translation flow (source flow hidden via html.hmy-tr-only, which also
// hides the in-progress source draft; the translation draft still shows).
function applyMode(m) {
  if (!MODES.includes(m)) return;
  mode = m;
  setPageButtons();
  win.webContents
    .executeJavaScript(
      `(function(){window.__hmyMode=${JSON.stringify(mode)};
        var de=document.documentElement;
        if(window.__hmyMode==='tr'){de.classList.add('hmy-tr-only');}
        else{de.classList.remove('hmy-tr-only');}
      })();`
    )
    .catch(() => {});
}

// Subtitle-card backdrop: bg in percent (0 = fully transparent, no card).
// The text itself is always opaque; a layer-injected rule with !important
// wins over the server page's defaults, and a later rule always supersedes.
function applyBg(alphaPct) {
  if (!Number.isInteger(alphaPct) || alphaPct < 0 || alphaPct > 60) return;
  bg = alphaPct;
  const a = (bg / 100).toFixed(2);
  // The card encloses the whole title bar (frame from 6px) and keeps that
  // frame (border + shadow) in BOTH modes -- only the FILL follows the
  // slider. The padding pair keeps the text where it always was (see the
  // base #box rule). The flows get a one-line min-height (em units follow
  // the font size) so an EMPTY card keeps exactly the size of a one-line
  // card -- the bottom edge no longer shrinks above the text line.
  const css = bg > 0
    ? `#box{top:6px!important;padding:36px 8px 8px!important;
         background:rgba(0,0,0,${a})!important;}
       #hmy-txt-flow{min-height:1.35em!important;}
       #hmy-tr-flow{min-height:1.25em!important;}`
    : `#box{top:6px!important;padding:36px 8px 8px!important;
         background:transparent!important;}
       #hmy-txt-flow{min-height:0!important;}
       #hmy-tr-flow{min-height:0!important;}`;
  win.webContents.insertCSS(css).catch(() => {});
  // keep the top-center slider in sync (any path that changes bg -- menu,
  // slider, startup -- converges on the same value)
  win.webContents
    .executeJavaScript(
      `(function(){var s=document.getElementById('hmy-bg-slider');
        if(s){s.value=${bg};} else { window.__hmyBg=${bg}; }
      })();`
    )
    .catch(() => {});
}

// Window width follows the width slider (native edge-drag resize is
// unavailable on this transparent/frameless window -- no resize handles).
// setBounds works reliably (probed). The window KEEPS its x at all times
// (left edge fixed, right edge stretches) so the left-anchored sliders
// never move under the cursor -- no jitter. The window stays where the
// user puts it; only the height keeps auto-fitting the card content.
function applyWidth(px) {
  const v = Math.max(WIN_W_MIN, Math.min(WIN_W_MAX, Math.round(px)));
  if (!win || win.isDestroyed()) return;
  const b = win.getBounds();
  win.setBounds({ x: b.x, y: b.y, width: v, height: b.height });
  diag("IPC: width " + v);
}

// Bold subtitle text (both flows): a layer-injected rule with !important
// wins over defaults; switching off re-inserts the normal weight. Font
// weight does not change line height, so fitHeight is unaffected.
function applyBold(on) {
  bold = !!on;
  const css = bold
    ? `#hmy-txt-flow,#hmy-tr-flow{font-weight:700!important;}`
    : `#hmy-txt-flow,#hmy-tr-flow{font-weight:400!important;}`;
  win.webContents.insertCSS(css).catch(() => {});
}

// Re-apply the CURRENT text-style state as ONE complete text-shadow rule.
// Called by every text-style menu radio: after the clicked tier is stored,
// textShadowList() merges the outside rim (描边/重墨) with the drop shadow
// (阴影) into a single list -- so choosing one tier never clobbers another
// (the same insertCSS "never remove, later rule wins" pattern as the rest).
function applyTextStyleSheet() {
  const ts = textShadowList(textShadow, textStroke, textInk);
  const decl = ts ? `text-shadow:${ts};` : "text-shadow:none;";
  win.webContents.insertCSS(`#hmy-txt-flow,#hmy-tr-flow{${decl}}`).catch(() => {});
}

// Drop-shadow tier (off / std / heavy).
function applyTextShadow(key) {
  if (!TEXT_SHADOW_CHOICES.some((c) => c.key === key)) return;
  textShadow = key;
  applyTextStyleSheet();
}

// 描边 (thin, 1px rim) vs 重墨 (thick bilibili rim): two thicknesses of the
// same outside-outline effect -- choosing one turns the other off.
function applyTextStroke(key) {
  textStroke = key === "thin" ? "thin" : "off";
  if (textStroke === "thin") textInk = "off";
  applyTextStyleSheet();
}
function applyTextInk(key) {
  textInk = key === "on" ? "on" : "off";
  if (textInk === "on") textStroke = "off";
  applyTextStyleSheet();
}

// Subtitle text opacity in percent (the backdrop is a separate setting).
function applyTextOpacity(pct) {
  if (!TEXT_OPACITY_CHOICES.includes(pct)) return;
  textOpacity = pct;
  win.webContents.insertCSS(`#hmy-txt-flow,#hmy-tr-flow{opacity:${(pct / 100).toFixed(2)};}`).catch(() => {});
}

function applyFontSize(px) {
  opts.size = px;
  // webContents.insertCSS() returns a Promise<CSSKey>; storing the promise
  // itself and later calling removeInsertedCSS(promise) throws
  // "Failed to serialize arguments". Fix: never remove -- just insert a NEW
  // rule. CSS is cascade-layered: a later-inserted rule with the same
  // specificity and !important wins over the earlier one, so re-inserting is
  // both simpler and always applies the newest size (no async race).
  const css = `#box{font-size:${px}px!important;} #hmy-txt-flow,#hmy-tr-flow{font-size:${px}px!important;}`;
  win.webContents.insertCSS(css).catch(() => {});
}

function applyFont(family) {
  opts.font = family;
  // insertCSS is "insert-only, later wins": switching BACK to the page
  // default must insert a rule too, otherwise the previously inserted
  // font-family !important rule stays in force forever (2026-10-19 report:
  // choosing 默认 after another font had no effect). The reset re-states the
  // overlay page's own stack (subtitle_server.py: #box font-family).
  const stack = family || '"Yu Gothic UI", "Meiryo", sans-serif';
  const css = `#box{font-family:${stack}!important;} #hmy-txt-flow,#hmy-tr-flow{font-family:${stack}!important;}`;
  win.webContents.insertCSS(css).catch(() => {});
}

// ---------------------------------------------------------------------------
// Settings panel (modern HTML panel window; replaces the native Menu.popup,
// whose OS-drawn look could not be styled and clashed with the dark card).
// One flat scrolling page in three sections; the current value of every
// setting is highlighted inline (no second-level submenus to hunt through).

// Panel page: inlined data-URL document (same packaging story as the
// makeCss/makeInitJs templates -- electron-builder's files list stays
// main.js/preload.js only). The choice constants (SIZE_CHOICES, ...) are the
// single source of truth: they are interpolated at open time, so a new tier
// added there shows up here automatically.
function panelHtml() {
  const state = panelState();
  // pill-grid row builder: one <button class="pill" data-act data-val>,
  // the selected tier carrying .on (amber fill)
  const pills = (act, choices, current, labels) => choices.map((c) => {
    const val = typeof c === "object" ? (c.family !== undefined ? c.family : c.key) : c;
    const lab = labels ? labels(val, c) : String(c);
    const on = val === current || (act === "font" && val === (current || ""));
    return `<button class="pill${on ? " on" : ""}" data-act="${act}" data-val="${String(val).replace(/"/g, "&quot;")}">${lab}</button>`;
  }).join("");
  const fontPills = FONT_CHOICES.map((f) =>
    `<button class="pill${(state.font || "") === f.family ? " on" : ""}" data-act="font" data-val="${f.family.replace(/"/g, "&quot;")}">${f.label}</button>`).join("");
  const langLabels = { en: "英语", zh: "中文", ko: "韩语", off: "关闭" };
  const lidPills = [
    `<button class="pill${state.lidMode === "auto" ? " on" : ""}" data-act="lid" data-val="auto">自动切换</button>`,
    ...LID_PILLS.map(([code, label]) =>
      `<button class="pill${state.lidMode === "manual" && state.lidModel === code ? " on" : ""}" data-act="lid" data-val="${code}">${label}</button>`),
  ].join("");
  const lidMoreOpts =
    `<option value="">更多模型…</option>`
    + LID_MORE.map(([code, label]) => `<option value="${code}">${label}</option>`).join("");
  return `<!doctype html>
<html><head><meta charset="utf-8">
<style>
  *{margin:0;padding:0;box-sizing:border-box;}
  html,body{background:transparent;font-family:'Segoe UI','Microsoft YaHei',sans-serif;
    -webkit-user-select:none;user-select:none;overflow:hidden;}
  /* Palette as variables: :root is the dark glass (matches the subtitle
     card); @media(prefers-color-scheme:light) overrides when Windows runs
     its light theme. The amber accent + red destructive colors are shared
     (both hold on light and dark). html.light forces the light palette for
     the ?theme=light preview probe. */
  /* color-scheme makes Chromium paint NATIVE controls (the "更多模型"
     select popup most visibly, plus scrollbars) with the current palette.
     Without it the closed select followed our variables but its popup used
     the browser default -- under a light Windows theme the language list
     stayed black (2026-10-21 report). */
  :root{color-scheme:dark;
    --bg:rgba(24,26,33,.92);--border:rgba(255,255,255,.09);
    --text:rgba(255,255,255,.95);--text-dim:rgba(255,255,255,.42);
    --lab:rgba(255,255,255,.85);--hint:rgba(255,255,255,.35);
    --pill-bg:rgba(255,255,255,.06);--pill-border:rgba(255,255,255,.14);
    --pill-text:rgba(255,255,255,.82);--pill-hover:rgba(255,255,255,.13);
    --pill-hover-border:rgba(255,255,255,.3);--track:rgba(255,255,255,.18);
    --scroll:rgba(255,255,255,.22);--close-dim:rgba(255,255,255,.55);
    --menu-bg:#20222a;--menu-text:#e8e8ec;
    --shadow:0 6px 18px rgba(0,0,0,.24),0 1px 4px rgba(0,0,0,.14);}
  html.light{color-scheme:light;--bg:rgba(248,249,252,.95);--border:rgba(0,0,0,.1);
    --text:#25272e;--text-dim:rgba(0,0,0,.4);
    --lab:rgba(0,0,0,.72);--hint:rgba(0,0,0,.38);
    --pill-bg:rgba(0,0,0,.045);--pill-border:rgba(0,0,0,.14);
    --pill-text:rgba(0,0,0,.75);--pill-hover:rgba(0,0,0,.09);
    --pill-hover-border:rgba(0,0,0,.28);--track:rgba(0,0,0,.16);
    --scroll:rgba(0,0,0,.22);--close-dim:rgba(0,0,0,.5);
    --menu-bg:#f6f7fa;--menu-text:#222;
    --shadow:0 6px 18px rgba(0,0,0,.16),0 1px 4px rgba(0,0,0,.08);}
  @media (prefers-color-scheme: light) {
    html:not(.dark){color-scheme:light;--bg:rgba(248,249,252,.95);--border:rgba(0,0,0,.1);
      --text:#25272e;--text-dim:rgba(0,0,0,.4);
      --lab:rgba(0,0,0,.72);--hint:rgba(0,0,0,.38);
      --pill-bg:rgba(0,0,0,.045);--pill-border:rgba(0,0,0,.14);
      --pill-text:rgba(0,0,0,.75);--pill-hover:rgba(0,0,0,.09);
      --pill-hover-border:rgba(0,0,0,.28);--track:rgba(0,0,0,.16);
      --scroll:rgba(0,0,0,.22);--close-dim:rgba(0,0,0,.5);
      --menu-bg:#f6f7fa;--menu-text:#222;
      --shadow:0 6px 18px rgba(0,0,0,.16),0 1px 4px rgba(0,0,0,.08);}
  }
  #panel{position:fixed;inset:8px;display:flex;flex-direction:column;
    background:var(--bg);
    -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
    border:1px solid var(--border);border-radius:12px;
    box-shadow:var(--shadow);
    animation:hmy-in .13s ease-out;}
  @keyframes hmy-in{from{opacity:0;transform:scale(.97) translateY(-4px);}
                    to{opacity:1;transform:none;}}
  /* the head doubles as the drag handle... */
  #head{display:flex;align-items:center;justify-content:space-between;
    padding:12px 16px 10px;flex:none;-webkit-app-region:drag;}
  /* ...but the close button inside it must stay clickable */
  #close{-webkit-app-region:no-drag;width:24px;height:24px;border:none;border-radius:6px;
    cursor:pointer;background:transparent;color:var(--close-dim);
    font-family:'Segoe MDL2 Assets';font-size:11px;line-height:24px;text-align:center;}
  #close:hover{background:rgba(220,60,60,.85);color:#fff;}
  #body{flex:1;overflow-y:auto;overflow-x:hidden;padding:0 16px 6px;scrollbar-width:thin;
    scrollbar-color:var(--scroll) transparent;}
  #body::-webkit-scrollbar{width:6px;}
  #body::-webkit-scrollbar-thumb{background:var(--scroll);border-radius:3px;}
  .sec{font-size:10px;font-weight:700;letter-spacing:2px;color:var(--text-dim);
    text-transform:uppercase;margin:14px 0 8px;}
  .row{display:flex;align-items:flex-start;justify-content:flex-start;
    min-height:30px;padding:4px 0;gap:18px 24px;flex-wrap:wrap;}
  /* a label and ITS controls stay one unbreakable unit: rows wrap between
     groups, never between a label and its pills (2026-10-19 report: the
     "显示模式" label stranded at a line end while its 双语/仅译文 pills
     wrapped to the next line) */
  .grp{display:flex;align-items:flex-start;gap:10px;flex:0 1 auto;min-width:0;}
  /* top-align the label with the FIRST pill row (multi-line pill grids
     otherwise leave the label floating mid-row) */
  .row .lab{font-size:12.5px;line-height:28px;color:var(--lab);flex:none;}
  .pills{display:flex;flex-wrap:wrap;gap:6px;min-width:0;}
  .pill{border:1px solid var(--pill-border);background:var(--pill-bg);
    color:var(--pill-text);font-size:12px;padding:5px 10px;border-radius:14px;
    cursor:pointer;font-family:inherit;transition:background .12s ease,border-color .12s ease,
    color .12s ease,transform .06s ease;}
  .pill:hover{background:var(--pill-hover);border-color:var(--pill-hover-border);}
  .pill:active{transform:translateY(1px);}
  .pill.on{background:#e8a87c;border-color:#e8a87c;color:#1d1410;font-weight:600;}
  .pill.on:hover{background:#f0b58a;border-color:#f0b58a;}
  .pill.dis{opacity:.35;cursor:default;pointer-events:none;}
  .switch{position:relative;width:38px;height:21px;border-radius:11px;flex:none;
    background:var(--track);border:none;cursor:pointer;transition:background .15s ease;
    margin-top:4px;}
  .switch::after{content:"";position:absolute;top:2.5px;left:3px;width:16px;height:16px;
    border-radius:50%;background:#fff;transition:left .15s ease;}
  .switch.on{background:#e8a87c;}
  .switch.on::after{left:19px;}
  #foot{flex:none;padding:9px 16px 12px;border-top:1px solid var(--border);
    display:flex;align-items:center;justify-content:space-between;}
  #hint{font-size:10.5px;color:var(--hint);max-width:260px;}
  #quit{border:1px solid rgba(220,80,70,.35);background:transparent;color:rgba(255,150,140,.9);
    font-size:12px;padding:5px 13px;border-radius:8px;cursor:pointer;font-family:inherit;
    transition:background .12s ease,color .12s ease;}
  #quit:hover{background:rgba(220,60,60,.85);border-color:rgba(255,120,110,.6);color:#fff;}
  html.light #quit{border-color:rgba(220,80,70,.45);color:#c2392c;}
  html.light #quit:hover{color:#fff;}
  /* 翻译 API form (URL / model / key + save&test) */
  #api-grid{display:flex;flex-direction:column;gap:8px;padding:2px 0;}
  .api-line{display:flex;align-items:center;gap:8px;}
  .api-line .lab{font-size:12.5px;color:var(--lab);flex:none;width:56px;line-height:28px;}
  .api-line .lab.auto{width:auto;}
  .api-field{flex:1;min-width:0;background:var(--pill-bg);border:1px solid var(--pill-border);
    color:var(--text);border-radius:8px;padding:6px 10px;
    font-family:'Segoe UI','Microsoft YaHei',sans-serif;font-size:12px;outline:none;
    transition:border-color .12s ease;}
  .api-field:focus{border-color:#e8a87c;}
  .api-field::placeholder{color:var(--hint);}
  #hmy-api-save{border:none;background:#e8a87c;color:#1d1410;font-weight:600;
    border-radius:8px;padding:6px 14px;cursor:pointer;font-family:inherit;font-size:12px;
    flex:none;transition:background .12s ease,opacity .12s ease;}
  #hmy-api-save:hover{background:#f0b58a;}
  #hmy-api-save:disabled{opacity:.5;cursor:default;}
  #hmy-api-status{font-size:11px;color:var(--hint);flex:1;min-width:80px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #hmy-api-status.ok{color:#3f9d63;}
  #hmy-api-status.err{color:#d05a4e;}
  /* recognition-model row: everyday models as pills + one dropdown for
     v3/omnilingual (the longer labels would wrap into a wall) */
  .lid-select{background:var(--pill-bg);border:1px solid var(--pill-border);
    color:var(--text);border-radius:14px;padding:3px 8px;font-family:inherit;
    font-size:12px;outline:none;height:27px;max-width:250px;cursor:pointer;}
  .lid-select:focus{border-color:#e8a87c;}
  /* popup rows follow the SAME variables as the panel (media-query light
     included -- the old html.light-only override left the list black under
     a light Windows theme); color-scheme above covers the native drawing */
  .lid-select option,.lid-select optgroup{background:var(--menu-bg);color:var(--menu-text);}
  #hmy-lid-status{font-size:11px;color:var(--hint);min-height:12px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;}
  #hmy-lid-status.err{color:#d05a4e;}
</style></head>
<body><div id="panel">
  <div id="head"><span class="title">字幕设置</span>
    <button id="close" title="关闭 (Esc)">&#xE8BB;</button></div>
  <div id="body">
    <div class="sec">识别模型</div>
    <div class="row"><span class="grp"><span class="lab">模型</span>
      <span class="pills">${lidPills}</span></span>
      <span class="grp"><span class="lab">更多</span>
      <select id="hmy-lid-more" class="lid-select"
        title="手动固定一个模型：引擎只保留该模型并释放其余模型；自动则恢复 LID 与全部模型">${lidMoreOpts}</select></span></div>
    <div class="row"><span id="hmy-lid-status"></span></div>

    <div class="sec">字幕样式</div>
    <div class="row"><span class="lab">字号</span>
      <span class="pills">${pills("size", SIZE_CHOICES, state.size)}</span></div>
    <div class="row"><span class="lab">字体</span>
      <span class="pills">${fontPills}</span></div>
    <div class="row"><span class="grp"><span class="lab">阴影</span>
      <span class="pills">${pills("textShadow", TEXT_SHADOW_CHOICES, state.textShadow, (v, c) => c.label)}</span></span>
      <span class="grp"><span class="lab">描边</span>
      <span class="pills">${pills("textStroke", TEXT_STROKE_CHOICES, state.textStroke, (v, c) => c.label)}</span></span>
      <span class="grp"><span class="lab">重墨</span>
      <span class="pills">${pills("textInk", TEXT_INK_CHOICES, state.textInk, (v, c) => c.label)}</span></span></div>
    <div class="row"><span class="grp"><span class="lab">粗体</span>
      <button class="switch${state.bold ? " on" : ""}" data-act="bold" data-toggle="1"></button></span>
      <span class="grp"><span class="lab">文字透明度</span>
      <span class="pills">${pills("textOpacity", TEXT_OPACITY_CHOICES, state.textOpacity, (v) => v + "%")}</span></span></div>

    <div class="sec">翻译</div>
    <div class="row"><span class="grp"><span class="lab">目标语言</span>
      <span class="pills">${pills("lang", LANGS, state.lang, (v) => langLabels[v] || v)}</span></span>
      <span class="grp"><span class="lab">通道</span>
      <span class="pills">
        <button class="pill${!state.apiMode ? " on" : ""}" data-act="apiMode" data-val="0">本地模型</button>
        <button class="pill${state.apiMode ? " on" : ""}${state.apiAvailable ? "" : " dis"}" data-act="apiMode" data-val="1"${state.apiAvailable ? "" : " title=\"未找到 openai_translate.json\""}>OpenAI API</button>
      </span></span>
      <span class="grp"><span class="lab">显示模式</span>
      <span class="pills">
        <button class="pill${state.mode === "both" ? " on" : ""}" data-act="mode" data-val="both">双语</button>
        <button class="pill${state.mode === "tr" ? " on" : ""}" data-act="mode" data-val="tr">仅译文</button>
      </span></span></div>

    <div class="sec">翻译 API（OpenAI 兼容）</div>
    <div id="api-grid">
      <div class="api-line"><span class="lab">地址</span>
        <input id="hmy-api-url" class="api-field" type="text" spellcheck="false"
               placeholder="https://api.siliconflow.cn/v1">
        <span class="lab auto">模型</span>
        <input id="hmy-api-model" class="api-field" type="text" spellcheck="false"
               placeholder="tencent/Hunyuan-MT-7B"></div>
      <div class="api-line"><span class="lab">密钥</span>
        <input id="hmy-api-key" class="api-field" type="password" spellcheck="false"
               placeholder="sk-...（本地模型可留空）">
        <button id="hmy-api-save">保存并测试</button>
        <span id="hmy-api-status"></span></div>
      <div class="api-line"><span class="lab">思考模式</span>
        <span class="pills" id="hmy-api-thinking"
              title="默认=不发送 enable_thinking（最兼容，严格 MT 端点如 Hunyuan-MT 用它）；开/关=随请求携带该参数，模型不支持时保存测试会报 HTTP 错误">
          <button class="pill tpill" data-thinking="default">默认（不发送）</button>
          <button class="pill tpill" data-thinking="on">开</button>
          <button class="pill tpill" data-thinking="off">关</button>
        </span>
        <span class="lab auto">上下文翻译</span>
        <button class="switch" id="hmy-api-context" title="带上下文翻译（最近对话作为参考）"></button></div>
    </div>

    <div class="sec">背景与窗口</div>
    <div class="row"><span class="grp"><span class="lab">背景</span>
      <span class="pills">${pills("bg", BG_MENU_CHOICES, state.bg, (v) => v === 0 ? "纯透明" : v + "%")}</span></span>
      <span class="grp"><span class="lab">点击穿透</span>
      <button class="switch${state.passthrough ? " on" : ""}" data-act="passthrough" data-toggle="1"></button></span></div>
  </div>
  <div id="foot">
    <span id="hint">穿透时左上角按钮区仍可点 / 可右键</span>
    <button id="quit">退出字幕窗</button>
  </div>
</div>
<script>
(function(){
  var qs = new URLSearchParams(location.search);
  // snapshot injected by openPanel() (data URLs cannot fetch anything)
  var S = ${JSON.stringify(state)};
  window.__hmySetState = function(next){
    S = next;
    document.querySelectorAll(".pill").forEach(function(p){
      var act = p.getAttribute("data-act"), val = p.getAttribute("data-val");
      if (!act) return; // form pills (hmy-api-thinking .tpill) manage their own state
      var cur = S[act];
      if (act === "apiMode" || act === "mode") return; // handled below (string/value kinds)
      var v = isNaN(Number(val)) ? val : Number(val);
      if (act === "font") { p.classList.toggle("on", (val === (S.font || ""))); return; }
      if (act === "lid") {
        p.classList.toggle("on", val === "auto"
          ? S.lidMode === "auto"
          : (S.lidMode === "manual" && val === S.lidModel));
        return;
      }
      if (act === "textShadow" || act === "textStroke" || act === "textInk" || act === "lang") {
        p.classList.toggle("on", val === cur); return;
      }
      p.classList.toggle("on", v === cur);
    });
    document.querySelectorAll('[data-act="apiMode"]').forEach(function(p){
      p.classList.toggle("on", (p.getAttribute("data-val") === "1") === !!S.apiMode);
    });
    document.querySelectorAll('[data-act="mode"]').forEach(function(p){
      p.classList.toggle("on", p.getAttribute("data-val") === S.mode);
    });
    document.querySelectorAll(".switch").forEach(function(s){
      var act = s.getAttribute("data-act");
      if (!act) return; // form switches (e.g. hmy-api-context) manage their own state
      s.classList.toggle("on", !!S[act]);
    });
    var api = document.querySelector('[data-act="apiMode"][data-val="1"]');
    if (api) api.classList.toggle("dis", !S.apiAvailable);
    // "更多模型" dropdown mirrors a manual v3/omni choice; the four pill
    // models leave it on the placeholder.
    var sel = document.getElementById("hmy-lid-more");
    if (sel) {
      var inPills = ${JSON.stringify(LID_PILLS.map((p) => p[0]))}.indexOf(S.lidModel) >= 0;
      sel.value = (S.lidMode === "manual" && S.lidModel && !inPills) ? S.lidModel : "";
    }
    var lid = document.getElementById("hmy-lid-status");
    if (lid) {
      lid.textContent = S.lidHint + (S.lidErr ? " · ✗ " + S.lidErr : "");
      lid.className = "lid-status" + (S.lidErr ? " err" : "");
    }
  };
  function apply(act, val){
    if (window.hmyPanel) window.hmyPanel.apply(act, val);
    if (!qs.get("e2e")) { /* live mode: main syncs state back */ }
  }
  document.addEventListener("click", function(ev){
    // API-form controls must be handled BEFORE the apply pills/switches:
    // they only take effect on 保存并测试, not immediately.
    var tp = ev.target.closest(".tpill");
    if (tp) { setThinking(tp.getAttribute("data-thinking")); return; }
    if (ev.target.closest("#hmy-api-context")) {
      document.getElementById("hmy-api-context").classList.toggle("on");
      return;
    }
    var el = ev.target.closest(".pill, .switch");
    if (el) {
      var act = el.getAttribute("data-act");
      var val = el.hasAttribute("data-toggle")
        ? !el.classList.contains("on")
        : el.getAttribute("data-val");
      apply(act, val);
      // optimistic local flip for toggles (main sends the authoritative state)
      if (el.hasAttribute("data-toggle")) el.classList.toggle("on", !!val);
      return;
    }
    if (ev.target.closest("#hmy-api-save")) { saveTest(); return; }
    if (ev.target.closest("#close")) { if (window.hmyPanel) window.hmyPanel.close(); }
    if (ev.target.closest("#quit")) { if (window.hmyPanel) window.hmyPanel.apply("quit", 1); }
  });
  // The "更多模型" dropdown applies on pick (no extra OK button); the empty
  // placeholder is ignored. Main syncs the selected pill back.
  var lidSel = document.getElementById("hmy-lid-more");
  if (lidSel) {
    lidSel.addEventListener("change", function(){
      if (this.value) apply("lid", this.value);
    });
  }
  // "保存并测试": test the typed URL/model/key with a live chat-completions
  // call FIRST; only a passing test writes openai_translate.json (main does
  // the merge+write+hot-reload). A failing test leaves the working config
  // untouched. The tri-state thinking choice (default/on/off) rides along:
  // "开/关" are sent in the TEST payload too, so a model that rejects the
  // field fails visibly here instead of silently after saving.
  function setThinking(v){
    document.querySelectorAll('#hmy-api-thinking .tpill').forEach(function(b){
      b.classList.toggle('on', b.getAttribute('data-thinking') === v);
    });
  }
  function thinkingValue(){
    var on = document.querySelector('#hmy-api-thinking .tpill.on');
    return on ? on.getAttribute('data-thinking') : 'default';
  }
  function saveTest(){
    var btn = document.getElementById('hmy-api-save');
    var st = document.getElementById('hmy-api-status');
    var vals = {
      base_url: document.getElementById('hmy-api-url').value.trim(),
      model: document.getElementById('hmy-api-model').value.trim(),
      api_key: document.getElementById('hmy-api-key').value,
      thinking: thinkingValue(),
      context_on: document.getElementById('hmy-api-context').classList.contains('on')
    };
    btn.disabled = true;
    st.className = '';
    st.textContent = '测试中…';
    (window.hmyPanel ? window.hmyPanel.apiSaveTest(vals) : Promise.reject(new Error('bridge missing')))
      .then(function(r){
        if (r && r.ok) {
          st.className = 'ok';
          var sample = r.sample ? String(r.sample) : '';
          var short = sample.length > 16 ? sample.slice(0, 16) + '…' : sample;
          st.textContent = '✓ 可用' + (short ? ' · 返回「' + short + '」' : '');
        } else {
          st.className = 'err';
          st.textContent = '✗ ' + ((r && r.error) || '未知错误');
        }
      })
      .catch(function(e){
        st.className = 'err';
        st.textContent = '✗ ' + ((e && e.message) || e);
      })
      .then(function(){ btn.disabled = false; });
  }
  // prefill the form from the project-root openai_translate.json
  if (window.hmyPanel && window.hmyPanel.apiGet) {
    window.hmyPanel.apiGet().then(function(c){
      if (!c) return;
      document.getElementById('hmy-api-url').value = c.base_url || '';
      document.getElementById('hmy-api-model').value = c.model || '';
      document.getElementById('hmy-api-key').value = c.api_key || '';
      setThinking(c.thinking || 'default');
      document.getElementById('hmy-api-context').classList.toggle('on', c.context_on !== false);
    }).catch(function(){});
  }
  __hmySetState(S);
})();
</script></body></html>`;
}

const PANEL_W = 860;   // panel window css width — deliberately WIDE and
                       // short (height < width): all pill groups (font size
                       // 9 / fonts 12 / backdrop 14) fit on one line each
                       // after the label abbreviations, so nothing scrolls
const PANEL_PAD = 14;  // popup offset below the subtitle window's top-left

let panelWin = null;   // the single settings-panel instance
let panelStateSeq = 0; // guards against a stale async apply ack
let panelFit = null;   // openPanel's fit closure, re-run when content changes

// Full settings snapshot for the panel renderer. Every apply* below already
// converges the subtitle window; the snapshot just mirrors the resulting
// state so the panel's checked pills stay truthful after each action.
function panelState() {
  return {
    size: opts.size,
    font: opts.font || "",
    bold,
    textShadow, textStroke, textInk, textOpacity,
    lang,
    apiMode, apiAvailable,
    mode,
    bg,
    passthrough,
    barAutohide,
    lidMode,
    lidModel,
    enTier: lidEnTier,
    lidErr,
    lidHint: lidMode === "manual" && lidModel
      ? "手动：" + lidModelLabel(lidModel) + "（"
        + (LID_MODEL_HINT[lidModel] || "") + "）· 其他模型已释放"
      : "自动：whisper-tiny 判定 + SenseVoice 复核（新语种需 2 秒确认）",
  };
}

// The panel is a second top-level window: skip the taskbar, no frame, no
// shadow (the CSS shadow inside the transparent window draws it), not
// resizable, and ALWAYS mapped at a concrete screen position by openPanel().
function openPanel() {
  if (panelWin && !panelWin.isDestroyed()) {
    panelWin.show();
    panelWin.focus();
    return;
  }
  const { workArea } = screen.getPrimaryDisplay();
  const winB = win && !win.isDestroyed() ? win.getBounds() : workArea;
  // default: under the subtitle window's top-left corner, nudged inside the
  // work area so the panel never opens half off-screen or over the taskbar
  let x = winB.x + PANEL_PAD;
  let y = winB.y + PANEL_PAD + BTN_Y0 + BTN_S;
  const PANEL_H = 700; // creation height; re-fitted to the real content below
  x = Math.max(workArea.x, Math.min(x, workArea.x + workArea.width - PANEL_W - 8));
  y = Math.max(workArea.y, Math.min(y, workArea.y + workArea.height - PANEL_H - 8));
  panelWin = new BrowserWindow({
    x, y,
    width: PANEL_W,
    height: Math.min(PANEL_H, workArea.height - 16),
    show: false,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload-panel.js"),
      backgroundThrottling: false,
    },
  });
  panelWin.setAlwaysOnTop(true, "screen-saver");
  panelWin.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(panelHtml()));
  // Fit the window to the REAL content height (static page, never grows):
  // measured body height + the 8px css inset on each side. #body is the
  // flex:1 overflow scroller, so #panel.scrollHeight only reports the
  // CLIPPED height -- sum the fixed chrome (head/foot offsetHeights) plus
  // #body's true scrollHeight. Runs TWICE: once at load and once 80ms
  // later, because the first measurement can race font/scrollbar layout
  // (a 2-3px error left visible scrollbars; 2026-10-19 report). The
  // overflow-y:auto fallback only matters when content exceeds the work
  // area. HMY_PANEL_LIGHT=1 forces the light palette (dev probe).
  const fitPanel = () => {
    if (!panelWin || panelWin.isDestroyed()) return;
    panelWin.webContents
      .executeJavaScript(
        // #body is a flex:1 scroller: when the CONTENT is shorter than the
        // window, body.scrollHeight equals the stretched clientHeight, not
        // the content height -- the fit could then only ever grow and the
        // window kept the initial 700px blank at the bottom (2026-10-19
        // report). Un-flex it and let it size to content for one
        // synchronous read, then restore (nothing paints in between).
        "(function(){var h=document.getElementById('head');" +
        "var b=document.getElementById('body');" +
        "var f=document.getElementById('foot');" +
        "var of=b.style.flex,oh=b.style.height,oo=b.style.overflow;" +
        "b.style.flex='none';b.style.height='auto';b.style.overflow='visible';" +
        "var ch=b.offsetHeight;" +
        "b.style.flex=of;b.style.height=oh;b.style.overflow=oo;" +
        "return [h.offsetHeight,ch,f.offsetHeight];})()"
      )
      .then(([head, body, foot]) => {
        if (!panelWin || panelWin.isDestroyed()) return;
        if (process.env.HMY_PANEL_LIGHT === "1") {
          panelWin.webContents.executeJavaScript(
            "document.documentElement.classList.add('light')").catch(() => {});
        }
        // +22 = 16px inset + 2px border + 4px safety against sub-pixel
        // scrollbar oscillation
        const fitH = Math.min(head + body + foot + 22, workArea.height - 16);
        const b = panelWin.getBounds();
        // growing the height must not push the bottom edge past the work
        // area (over the taskbar): reclaim y so the bottom stays inside
        const fitY = Math.max(workArea.y,
          Math.min(b.y, workArea.y + workArea.height - fitH - 8));
        panelWin.setBounds({ x: b.x, y: fitY, width: PANEL_W, height: fitH });
        diag("panel: fitted h=" + fitH + " y=" + fitY + " (head=" + head + " body=" + body + " foot=" + foot + ")");
      })
      .catch(() => {});
  };
  panelWin.webContents.once("did-finish-load", () => {
    panelFit = fitPanel;
    fitPanel();
    setTimeout(fitPanel, 80);
  });
  // NOTE: no blur-close. The panel is a persistent topmost tool window
  // (2026-10-19 user request: clicking elsewhere must not dismiss it while
  // adjusting subtitles); it closes via its ✕, Esc, or the gear toggle.
  panelWin.once("closed", () => { panelWin = null; panelFit = null; });
  panelWin.once("ready-to-show", () => panelWin.showInactive());
  // showInactive: never steal focus from the user's typing app; the first
  // click IN the panel focuses it via the webContents click.
  panelWin.webContents.on("before-input-event", (_e, input) => {
    if (input.type === "keyDown" && input.key === "Escape") {
      diag("panel: esc -> close");
      closePanel();
    }
  });
}

function closePanel() {
  if (panelWin && !panelWin.isDestroyed()) {
    try { panelWin.close(); } catch (_) {}
  }
  panelWin = null;
}

function showMenu() {
  diag("show-menu -> panel");
  openPanel();
}

// Push the fresh state snapshot to an open panel after any apply*. Re-fit
// the window height too: state changes can alter row content (e.g. the API
// test status line) and a slightly-grown body must lengthen the window
// instead of showing a scrollbar (2026-10-19 report).
function syncPanel() {
  if (!panelWin || panelWin.isDestroyed() || !panelWin.isVisible()) return;
  const seq = ++panelStateSeq;
  panelWin.webContents
    .executeJavaScript(`window.__hmySetState(${JSON.stringify(panelState())}, ${seq})`)
    .catch(() => {});
  if (panelFit) setTimeout(panelFit, 40);
}

// IPC from the panel renderer: read state / apply one setting / self-close.
// Every apply goes through THE SAME functions the old native menu used, so
// behavior (incl. server notification + button sync) is byte-identical.
function registerPanelIpc() {
  ipcMain.handle("hmy:panel-get-state", () => panelState());
  ipcMain.handle("hmy:panel-api-get", () => readApiConfig());
  // "保存并测试": live-test the typed endpoint FIRST; only a passing test
  // merges {base_url, model, api_key} into openai_translate.json (all other
  // fields preserved), re-detects availability, and hot-applies the channel
  // on the running server (apply_translation_spec re-reads the file since
  // the 2026-10-18 reload fix). A failing test leaves the working config
  // untouched.
  ipcMain.handle("hmy:panel-api-save-test", async (_e, values) => {
    const base = String((values && values.base_url) || "").trim().replace(/\/+$/, "");
    const model = String((values && values.model) || "").trim();
    const key = String((values && values.api_key) || "").trim();
    const thinking = String((values && values.thinking) || "default"); // default|on|off
    const contextOn = !(values && values.context_on === false);
    if (!base || !model) return { ok: false, error: "地址和模型不能为空" };
    if (!/^https?:\/\//i.test(base)) return { ok: false, error: "地址需以 http:// 或 https:// 开头" };
    // 1) live test against the OpenAI-compatible chat endpoint. The thinking
    // choice rides along when explicit: a model that rejects the field
    // (Hunyuan-MT: HTTP 400 20015) must fail HERE, not after saving.
    let sample = "";
    try {
      const url = /\/chat\/completions$/.test(base) ? base : base + "/chat/completions";
      const ctrl = new AbortController();
      // reasoning models can take longer to produce their first content token
      // (thinking runs inside max_tokens): give the explicit-thinking test
      // more room so a slow-but-working endpoint is not reported as a failure
      const timeoutMs = thinking === "default" ? 10000 : 15000;
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      const payload = {
        model,
        messages: [{ role: "user", content: "ping" }],
        max_tokens: thinking === "default" ? 8 : 32,
        temperature: 0,
      };
      if (thinking === "on") payload.enable_thinking = true;
      else if (thinking === "off") payload.enable_thinking = false;
      let resp;
      try {
        resp = await fetch(url, {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" },
                              key ? { Authorization: "Bearer " + key } : {}),
          body: JSON.stringify(payload),
          signal: ctrl.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      if (!resp.ok) {
        let detail = "";
        try { detail = (await resp.text()).slice(0, 160).replace(/\s+/g, " "); } catch (_) {}
        diag("panel: api test failed HTTP " + resp.status);
        return { ok: false, error: "HTTP " + resp.status + (detail ? " · " + detail : "") };
      }
      try {
        const j = await resp.json();
        sample = String((j.choices && j.choices[0] && j.choices[0].message
                         && j.choices[0].message.content) || "").trim().slice(0, 40);
      } catch (_) { /* 2xx without JSON body: still usable */ }
    } catch (e) {
      const msg = (e && e.name === "AbortError") ? "超时（" + Math.round(timeoutMs / 1000) + " 秒）"
        : String((e && e.message) || e);
      diag("panel: api test error " + msg);
      // a slow reasoning model is "working" but useless for live subtitles:
      // say so instead of leaving a bare timeout
      const hint = (e && e.name === "AbortError" && thinking !== "default")
        ? "（端点可能在思考；推理模型用于实时字幕建议选「默认」或「关」）" : "";
      return { ok: false, error: msg + hint };
    }
    // 2) test passed: merge-save into the project-root config
    try {
      const root = findProjectRoot(launchDir());
      if (!root) return { ok: false, error: "找不到项目根目录（openai_translate.json 位置）" };
      const p = path.join(root, "openai_translate.json");
      let cfg = {};
      try {
        cfg = JSON.parse(fs.readFileSync(p, "utf8"));
      } catch (_) { cfg = {}; }
      if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) cfg = {};
      cfg.base_url = base;
      cfg.model = model;
      cfg.api_key = key;
      // tri-state thinking: default = the key must be ABSENT (don't send the
      // field -- strict MT endpoints reject it), on/off = explicit boolean
      if (thinking === "default") delete cfg.enable_thinking;
      else cfg.enable_thinking = thinking === "on";
      // context switch: 0 disables translation context; turning it back on
      // restores the previous count (>0) or the load_config default 6
      const prevN = Number(cfg.context_n);
      const keepN = Number.isFinite(prevN) && prevN > 0 ? prevN : 6;
      cfg.context_n = contextOn ? keepN : 0;
      fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n", "utf8");
      apiAvailable = detectApiConfig();
      if (apiAvailable) apiMode = true;
      diag("panel: api config saved -> " + p + " available=" + apiAvailable);
      try { notifyServerTranslation(translateSpec(lang)); } catch (_) {}
      syncPanel();
      return { ok: true, sample };
    } catch (e) {
      return { ok: false, error: "保存失败: " + ((e && e.message) || e) };
    }
  });
  ipcMain.on("hmy:panel-apply", (_e, action, value) => {
    try {
      switch (action) {
        case "size": applyFontSize(parseInt(value, 10)); break;
        case "font": applyFont(value); break;
        case "bold": applyBold(!!value); break;
        case "textShadow": applyTextShadow(value); break;
        case "textStroke": applyTextStroke(value); break;
        case "textInk": applyTextInk(value); break;
        case "textOpacity": applyTextOpacity(parseInt(value, 10)); break;
        case "lang":
          lang = value;
          notifyServerTranslation(translateSpec(lang));
          setPageButtons();
          win.webContents.executeJavaScript(`(function(){window.__hmyLang=${JSON.stringify(lang)};if(window.__hmyShowTr)window.__hmyShowTr();})();`).catch(() => {});
          break;
        case "apiMode": applyApiMode(!!value); break;
        case "mode": applyMode(value); break;
        case "lid": applyLidMode(value); break;
        case "bg": applyBg(parseInt(value, 10)); break;
        case "passthrough": applyPassthrough(!!value); break;
        case "quit": app.quit(); break;
        default: diag("panel: unknown action " + action);
      }
    } catch (e) {
      diag("panel apply error: " + (e && e.message));
    }
    syncPanel();
  });
  ipcMain.on("hmy:panel-close", () => closePanel());
}

// ---------------------------------------------------------------------------
// Startup notices. Three situations used to leave a blank Chromium error page
// on screen, which reads as "nothing happened":
//   1. the exe is NOT inside a complete package (no scripts/ + .venv/ above
//      it) -> findProjectRoot() fails, no server can be started;
//   2. a freshly spawned server needs 15-30 s to load its models -- the window
//      now shows a "starting" page and swaps to the overlay when 8833 answers;
//   3. the overlay URL fails to load mid-session (server died, port taken...).
// All three render a readable page inside the window AND raise one dialog.
// ---------------------------------------------------------------------------
function noticeHtml(title, lines, footer) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return "<!doctype html><meta charset='utf-8'><style>"
    + "html,body{margin:0;background:#1b1b1f;color:#e8e8ea;"
    + "font-family:'Microsoft YaHei','Segoe UI',sans-serif;overflow:hidden}"
    + "#n{padding:14px 16px}"
    + "h1{margin:0 0 8px;font-size:15px;color:#ffb86b}"
    + "p{margin:4px 0;font-size:12.5px;line-height:1.45}"
    + ".dim{color:#9a9aa2;font-size:11px;word-break:break-all;white-space:pre-wrap}"
    + "</style><div id='n'><h1>" + esc(title) + "</h1>"
    + lines.map((l) => "<p>" + esc(l) + "</p>").join("")
    + "<p class='dim'>" + esc(footer) + "</p></div>";
}

function noticePageUrl() {
  const html = noticeHtml(
    "找不到项目目录（缺 scripts\\ 与 .venv\\）",
    [
      "这个 exe 是整套 hayamimi 包的一部分，必须和 scripts\\、.venv\\、models\\ 待在同一个项目根下。",
      "· 请把整个包【完整解压】（不要只把 exe 拷出来单独运行），再双击 desktop-subtitle\\早耳字幕.exe",
      "· 刚解压的话，先运行一次包根目录的 首次运行.bat",
      "· 按 ✕ 按钮关闭此窗口；详见包内 docs\\PORTABLE.md",
    ],
    "exe: " + process.execPath + "\n搜索起点: " + launchDir());
  return "data:text/html;charset=utf-8," + encodeURIComponent(html);
}

function statusPageUrl() {
  const html = noticeHtml(
    "正在启动字幕服务器…",
    [
      "首次启动需要加载语音模型，大约 15–30 秒（切换音频源后重启也要这么久），请稍候。",
      "· 服务器就绪后本窗口会自动切到字幕页，无需操作",
      "· 若长时间停在此页：端口 8833 可能被占用，或服务器启动失败",
      "· 日志：%TEMP%\\hayamimi-serve.log / hayamimi-serve.err.log",
      "· 也可双击 desktop-subtitle\\启动早耳.bat 手动起服务器；按 ✕ 关闭本窗口",
    ],
    "exe: " + process.execPath + "\nproject root: " + (foundRoot || "?")
  );
  return "data:text/html;charset=utf-8," + encodeURIComponent(html);
}

function showRootNotice() {
  try {
    dialog.showMessageBox(win, {
      type: "warning",
      title: "hayamimi - 启动失败",
      message: "找不到项目目录（缺 scripts\\ 与 .venv\\）",
      detail: "这个 exe 必须和 scripts\\、.venv\\、models\\ 待在同一个项目根下。\n\n"
        + "· 请重新【完整解压】整合包（不要只拷贝 exe 单独运行），然后双击 "
        + "desktop-subtitle\\早耳字幕.exe\n"
        + "· 若刚解压，请先运行一次包根目录的 首次运行.bat",
      buttons: ["知道了"],
      noLink: true,
    }).catch(() => {});
  } catch (_) {}
}

function showServerNotice(url, code, desc) {
  try {
    dialog.showMessageBox(win, {
      type: "warning",
      title: "hayamimi - 服务器未启动",
      message: "无法连接转写服务器：" + url,
      detail: "服务器未就绪（" + code + " " + desc + "）。\n\n"
        + "· 端口 8833 可能被占用，或服务器启动失败\n"
        + "· 日志：%TEMP%\\hayamimi-serve.log / hayamimi-serve.err.log\n"
        + "· 也可双击 desktop-subtitle\\启动早耳.bat 先手动起服务器",
      buttons: ["知道了"],
      noLink: true,
    }).catch(() => {});
  } catch (_) {}
}

app.whenReady().then(() => {
  opts = parseArgs();
  lang = opts.lang;
  audioInput = opts.input; // serverSpawnArgs reads it (startUpFlow spawns later)

  // API-translation availability: a usable openai_translate.json in the
  // project root (one directory above this app) enables the "API" channel
  // button; without it the button stays greyed/local and behavior is the
  // pre-API version.
  apiAvailable = detectApiConfig();
  if (apiAvailable) { diag("api config present: API channel enabled"); }
  else { diag("no usable openai_translate.json: API channel disabled"); }
  // Default the CHANNEL to whatever the launcher does: translation now
  // starts OFF (the .bat defaults to empty --translate and so does this
  // window), so the channel preference only matters once the user turns
  // translation on. With a usable config the API channel is the default
  // (matches the documented api:zh launcher setup); without one,
  // apiAvailable=false -> apiMode=false -> local.
  apiMode = apiAvailable;
  mode = MODES.includes(opts.mode) ? opts.mode : "both"; // (--mode arg if ever given)
  bg = Number.isInteger(opts.bg) && opts.bg >= 0 && opts.bg <= 60 ? opts.bg : 1;
  bold = !!opts.bold;
  textShadow = TEXT_SHADOW_CHOICES.some((c) => c.key === opts.textShadow) ? opts.textShadow : "std";
  textStroke = opts.textStroke === "thin" ? "thin" : "off";
  textInk = opts.textInk === "on" ? "on" : "off";
  textOpacity = TEXT_OPACITY_CHOICES.includes(opts.textOpacity) ? opts.textOpacity : 100;
  if (textInk === "on") textStroke = "off"; // 重墨/描边 share one property
  barAutohide = opts.barAutohide !== false; // --no-bar-autohide keeps the bar pinned

  const { workArea } = screen.getPrimaryDisplay();
  const winW = Math.min(opts.width, workArea.width);
  const winH = Math.min(opts.height, workArea.height);
  const winX = workArea.x + Math.floor((workArea.width - winW) / 2);
  const winY = workArea.y + workArea.height - winH;

  ipcMain.on("hmy:show-menu", () => {
    diag("IPC: show-menu");
    // gear button toggles: an open panel closes, a closed one opens (the
    // panel no longer auto-closes on blur, so the gear is the quick
    // dismiss/reopen handle). Right-click still opens/focuses without
    // closing (see win.webContents context-menu -> showMenu()).
    if (panelWin && !panelWin.isDestroyed() && panelWin.isVisible()) {
      diag("panel: gear toggle -> close");
      closePanel();
    } else {
      openPanel();
    }
  });
  registerPanelIpc();
  ipcMain.on("hmy:toggle-passthrough", () => { diag("IPC: toggle-passthrough"); applyPassthrough(!passthrough); });
  ipcMain.on("hmy:toggle-pin", () => {
    // pin button: armed (auto-hide) <-> pinned (bar never hides)
    barAutohide = !barAutohide;
    diag("IPC: toggle-pin -> barAutohide=" + barAutohide);
    refreshMouseMode();
    setPageButtons();
  });
  ipcMain.on("hmy:cycle-lang", () => { diag("IPC: cycle-lang"); cycleLang(); });
  ipcMain.on("hmy:cycle-api", () => { diag("IPC: cycle-api"); cycleApi(); });
  ipcMain.on("hmy:toggle-mode", () => { diag("IPC: toggle-mode"); applyMode(mode === "both" ? "tr" : "both"); });
  ipcMain.on("hmy:cycle-input", () => { diag("IPC: cycle-input"); cycleAudioInput(); });
  ipcMain.on("hmy:minimize", () => { diag("IPC: minimize"); if (win && !win.isDestroyed()) win.minimize(); });
  ipcMain.on("hmy:close", () => { diag("IPC: close"); app.quit(); });
  ipcMain.on("hmy:set-bg", (_e, v) => { diag("IPC: set-bg " + v); applyBg(parseInt(v, 10)); });
  ipcMain.on("hmy:set-width", (_e, v) => { applyWidth(parseInt(v, 10)); });
  // Height-only auto-fit: the window WIDTH stays fixed (text wraps instead),
  // the HEIGHT follows the subtitle+translation content. The renderer polls
  // every 500ms, and we debounce with a threshold so this is strictly one-way
  // (no resize<->observer feedback loop -- unlike the removed MutationObserver
  // version this cannot deadlock).
  let lastAutoH = 0;
  ipcMain.on("hmy:resize", (_e, w, h) => {
    if (!win || win.isDestroyed()) return;
    // The floor is MIN_WIN_H (not opts.height=130): keeping the initial
    // height as a floor left the window ~30-70px taller than the card (the
    // empty/one-line states), i.e. a large draggable dead zone below the
    // backdrop. With the true floor the window hugs the card content.
    h = Math.max(MIN_WIN_H, Math.min(Math.round(h), workArea.height));
    if (Math.abs(h - lastAutoH) < 4) return; // debounce: ignore tiny jitters
    lastAutoH = h;
    // setSize is unreliable on this transparent/frameless window (probe:
    // setBounds works, setSize's result was ambiguous). Use setBounds with
    // the CURRENT x/y/width so a user-dragged width is preserved; only the
    // height follows the content.
    const b = win.getBounds();
    win.setBounds({ x: b.x, y: b.y, width: b.width, height: h });
    diag("IPC: resize h=" + h);
  });

  win = new BrowserWindow({
    width: winW,
    height: winH,
    x: winX,
    y: winY,
    show: false, // stay hidden until the server is ready (startUpFlow);
    // loadOverlay() reveals the window, error paths call win.show() themselves
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: true, // user can drag the edge/corner to change WIDTH (text
    // re-wraps); the HEIGHT keeps auto-fitting the card content (fitHeight
    // re-asserts it every 500ms). Also unblocks programmatic setSize, which
    // resizable:false silently rejected on Windows (window stayed at its
    // initial height no matter what fitHeight reported).
    hasShadow: false,
    focusable: true,
    webPreferences: {
      backgroundThrottling: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  win.setAlwaysOnTop(true, "screen-saver");
  passthrough = opts.passthrough;

  win.webContents.on("context-menu", (event) => {
    event.preventDefault();
    showMenu();
  });

  // Mid-session overlay load failures (server died, port taken, ...): say so
  // instead of showing Chromium's error page, which looks like "nothing
  // happened". Suppressed while the warm-up poll is running -- during that
  // phase 8833 is simply not up yet and no overlay load has been attempted.
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (isMainFrame === false) return;
    if (warmPollTimer) return;
    diag("did-fail-load code=" + code + " " + desc + " url=" + url);
    if (!failNotified) {
      failNotified = true;
      showServerNotice(url, code, desc);
    }
  });

  win.webContents.on("did-finish-load", async () => {
    diag("did-finish-load URL=" + win.webContents.getURL());
    // the initial empty document can finish loading before startUpFlow() has
    // decided what to show; nothing to bootstrap yet
    if (win.webContents.getURL() === "about:blank") return;
    if (serverRootMissing) {
      // not inside a complete package: there is no overlay page to style and no
      // server to talk to -- the readable notice page is already loaded, just
      // give it room and make sure the user notices it
      diag("incomplete package: showing the root notice");
      const nb = win.getBounds();
      win.setBounds({ x: nb.x, y: nb.y, width: nb.width, height: 250 });
      showRootNotice();
      return;
    }
    if (win.webContents.getURL().startsWith("data:")) {
      // warm-up / not-ready page: nothing to bootstrap until the overlay loads
      diag("status page shown; waiting for the server");
      const nb = win.getBounds();
      win.setBounds({ x: nb.x, y: nb.y, width: nb.width, height: 230 });
      return;
    }
    win.webContents.on("console-message", (_e, _l, message) => {
      diag("renderer: " + message);
    });
    try {
      await win.webContents.insertCSS(makeCss());
      diag("css injected");
    } catch (e) {
      diag("insertCSS FAILED: " + (e && e.message));
    }
    try {
      await win.webContents.executeJavaScript(makeInitJs());
      diag("init js injected");
    } catch (e) {
      diag("init js FAILED: " + (e && e.message));
    }
    try { applyPassthrough(passthrough); } catch (e) { diag("applyPassthrough err " + e.message); }
    try { applyMode(mode); } catch (e) { diag("applyMode err " + e.message); }
    try { applyBg(bg); } catch (e) { diag("applyBg err " + e.message); }
    try { applyBold(bold); } catch (e) { diag("applyBold err " + e.message); }
    try { applyTextShadow(textShadow); } catch (e) { diag("applyTextShadow err " + e.message); }
    try { applyTextStroke(textStroke); } catch (e) { diag("applyTextStroke err " + e.message); }
    try { applyTextInk(textInk); } catch (e) { diag("applyTextInk err " + e.message); }
    try { applyTextOpacity(textOpacity); } catch (e) { diag("applyTextOpacity err " + e.message); }
    try { setPageButtons(); } catch (e) { diag("setPageButtons err " + e.message); }
    // push the CURRENT title-bar state: the poll only sends on change, so a
    // page that loads while the bar should be hidden needs this first push
    try { win.webContents.send("hmy:bar", barVisible); } catch (e) { diag("bar push err " + e.message); }
    diag("bootstrap done");
    // Push our channel+language to the server ONCE at startup so the UI and
    // the server agree from the very beginning. The .bat may have started the
    // server with api:zh while this window loaded with local -- without this
    // sync the server kept API-translating ANY language while the button said
    // 本地 (the "local mode translated non-Japanese" report). After this the
    // server follows exactly what the button shows: local -> ja only, API ->
    // any source.
    notifyServerTranslation(translateSpec(lang));
  });

  // Load nothing yet: startUpFlow() probes/spawns the server first and drives
  // the window (warm-up page -> overlay, or the appropriate notice).
  startUpFlow();

  pollTimer = setInterval(refreshMouseMode, 40);

  // no global Esc shortcut: a global Esc quit the window while the user was
  // typing Esc in ANY app (browser forms, games). Closing is the title bar's
  // ✕ only; the other Ctrl+Alt+X combos are unlikely to collide and stay.
  globalShortcut.register("Control+Alt+D", () => applyPassthrough(!passthrough));
  globalShortcut.register("Control+Alt+L", () => cycleLang());
  globalShortcut.register("Control+Alt+M", () => applyMode(mode === "both" ? "tr" : "both"));
});

app.on("before-quit", () => {
  // Close the subtitle window == run 停止早耳.bat: stop the transcribe
  // server unconditionally, whether we spawned it on startup or it was
  // already running (someone started it first, then opened this window).
  // Only processes whose command line matches "realtime_transcribe" are
  // killed -- other pythons (MCP servers, etc.) are left alone.
  diag("before-quit: stopping transcribe servers (停止早耳.bat semantics)");
  closePanel();
  serverKillSync();
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (pollTimer) clearInterval(pollTimer);
});

app.on("window-all-closed", () => app.quit());