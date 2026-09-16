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
//   Ctrl+Alt+D toggle   Ctrl+Alt+L cycle language   Esc quit
const { app, BrowserWindow, screen, globalShortcut, Menu, ipcMain } = require("electron");
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
const LANGS = ["en", "zh", "ko", "off"];
const LANG_LABEL = { en: "EN", zh: "ZH", ko: "KO", off: "OFF" };
const SIZE_CHOICES = [12, 16, 20, 24, 32, 40, 48, 56, 64];
const FONT_CHOICES = [
  { label: "默认（跟随页面）", family: "" },
  { label: "微软雅黑", family: '"Microsoft YaHei", sans-serif' },
  { label: "等线", family: '"DengXian", sans-serif' },
  { label: "黑体", family: '"SimHei", sans-serif' },
  { label: "宋体", family: '"SimSun", serif' },
  { label: "楷体", family: '"KaiTi", serif' },
  { label: "幼圆", family: '"YouYuan", sans-serif' },
  { label: "隶书", family: '"LiSu", serif' },
  { label: "华文行楷", family: '"STXingkai", serif' },
  { label: "Arial", family: "Arial, sans-serif" },
  { label: "Georgia", family: "Georgia, serif" },
  { label: "Consolas 等宽", family: "Consolas, monospace" },
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

function autoStartServer(o) {
  probeServer(1200, (up) => {
    if (up) {
      diag("server already up; not spawning");
      return;
    }
    spawnServer(false);
  });
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
  const kept = [];
  for (let i = 0; i < raw.length; i++) {
    const a = raw[i];
    const spaceForm = a === "--input" || (applyButtonState && a === "--translate");
    const eqForm = a.startsWith("--input=") || (applyButtonState && a.startsWith("--translate="));
    if (spaceForm || eqForm) {
      if (spaceForm && raw[i + 1] && !raw[i + 1].startsWith("--")) i++; // eat the value
      continue;
    }
    kept.push(a);
  }
  const args = [path.join("scripts", "realtime_transcribe.py"), "--serve", "8833"];
  if (audioInput !== "mic") args.push("--input", audioInput);
  if (applyButtonState) {
    const spec = translateSpec(lang);
    if (spec) args.push("--translate", spec);
  }
  return args.concat(kept);
}

function spawnServer(applyButtonState) {
  const root = findProjectRoot(launchDir());
  if (!root) {
    diag("project root not found near exe; skipping server autostart");
    return false;
  }
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
let lang = "off"; // default: no translation (button cycles EN/ZH/KO/OFF)
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
    //                   share the screen. Each confirmed segment keeps its own
    //                   8s lifetime, then fades out.
    //   #hmy-tr-flow    translations: one segment per confirmed source with
    //                   the same 8s lifetime + an in-progress draft at the end.
    // 'segs' maps seq -> {srcEl, trEl, timer} so a late 'translation' (API is
    // slow) can still attach to its segment.
    var box = document.getElementById('box');
    var txtFlow = null, trFlow = null, txtPart = null, trDraft = null;
    var segs = {};        // seq -> {srcEl, trEl, timer}
    var lastSeq = 0;
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
      var ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }

      if (ev.type === 'partial') {
        // current in-progress source text rides inline at the end of the
        // source flow (same-line look, mirroring #partial-line).
        if (txtPart) txtPart.textContent = ev.text;
        return;
      }
      if (ev.type === 'partial_translation') {
        // current in-progress draft translation rides inline at the end of
        // the translation flow; never cached in __hmyTr (that map only
        // holds confirmed translations).
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
        // promote the current draft to a confirmed segment: append to the
        // source flow after older finals, start its 8s lifetime, and give
        // it an empty translation row the late 'translation' will fill.
        var seq = (ev.seq !== undefined && ev.seq !== null) ? ev.seq : (++lastSeq);
        if (seq > lastSeq) lastSeq = seq;
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

        var segObj = { srcEl: srcEl, trEl: trEl, timer: null };
        segs[seq] = segObj;
        // each confirmed segment has its own 8s lifetime, then fades out and
        // is removed -- several finals can share the screen in the meantime.
        segObj.timer = setTimeout(function(){
          // no fade-out: remove immediately after the 8s lifetime (a fade
          // felt laggy -- the text visibly sat there transitioning).
          if (srcEl && srcEl.parentNode) srcEl.parentNode.removeChild(srcEl);
          if (trEl && trEl.parentNode) trEl.parentNode.removeChild(trEl);
          var ds = srcEl.getAttribute && srcEl.getAttribute('data-seq');
          if (ds !== null && ds !== undefined && ds !== '') delete segs[String(ds)];
        }, 8000);
        // keep a reverse pointer for cleanup (simplest: read data-seq)
        srcEl.setAttribute('data-seq', String(seq));
        return;
      }
    };
    // helper: map a source element back to its seq via data-seq
    function s2seq(el) {
      if (!el) return null;
      var d = el.getAttribute && el.getAttribute('data-seq');
      return d !== null && d !== undefined && d !== '' ? String(d) : null;
    }
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
  const css = family ? `#box{font-family:${family}!important;} #hmy-txt-flow,#hmy-tr-flow{font-family:${family}!important;}` : "";
  win.webContents.insertCSS(css).catch(() => {});
}

function showMenu() {
  const template = [
    {
      label: "字号",
      submenu: SIZE_CHOICES.map((px) => ({
        label: px + " px", type: "radio", checked: opts.size === px,
        click: () => applyFontSize(px),
      })),
    },
    {
      label: "字体",
      submenu: FONT_CHOICES.map((f) => ({
        label: f.label, type: "radio", checked: opts.font === f.family,
        click: () => applyFont(f.family),
      })),
    },
    {
      label: "字幕粗体",
      type: "checkbox",
      checked: bold,
      click: (i) => applyBold(i.checked),
    },
    {
      label: "阴影",
      submenu: TEXT_SHADOW_CHOICES.map((c) => ({
        label: c.label, type: "radio", checked: textShadow === c.key,
        click: () => applyTextShadow(c.key),
      })),
    },
    {
      label: "描边",
      submenu: TEXT_STROKE_CHOICES.map((c) => ({
        label: c.label, type: "radio", checked: textStroke === c.key,
        click: () => applyTextStroke(c.key),
      })),
    },
    {
      label: "重墨",
      submenu: TEXT_INK_CHOICES.map((c) => ({
        label: c.label, type: "radio", checked: textInk === c.key,
        click: () => applyTextInk(c.key),
      })),
    },
    {
      label: "文字透明度",
      submenu: TEXT_OPACITY_CHOICES.map((p) => ({
        label: p + "%", type: "radio", checked: textOpacity === p,
        click: () => applyTextOpacity(p),
      })),
    },
    {
      label: "翻译语言",
      submenu: LANGS.map((l) => ({
        label: { en: "英语 EN", zh: "中文 ZH", ko: "韩语 KO", off: "关闭 OFF" }[l],
        type: "radio", checked: lang === l,
        click: () => {
          lang = l;
          notifyServerTranslation(translateSpec(lang)); // also switch server-side
          setPageButtons();
          win.webContents.executeJavaScript(`(function(){window.__hmyLang=${JSON.stringify(lang)};if(window.__hmyShowTr)window.__hmyShowTr();})();`).catch(() => {});
        },
      })),
    },
    {
      label: "翻译通道",
      submenu: [
        { label: "本地模型", type: "radio", checked: !apiMode,
          enabled: true, click: () => applyApiMode(false) },
        { label: "OpenAI API", type: "radio", checked: apiMode,
          enabled: apiAvailable,
          click: () => applyApiMode(true) },
      ],
      // API entry is disabled (greyed) and not toggleable when no config file
      // exists; label reflects that in the menu label itself.
      ...(apiAvailable ? {} : { toolTip: "未找到 openai_translate.json，API 通道不可用" }),
    },
    {
      label: "显示模式",
      submenu: [
        { label: "双语（原文 + 译文）", type: "radio", checked: mode === "both",
          click: () => applyMode("both") },
        { label: "仅译文", type: "radio", checked: mode === "tr",
          click: () => applyMode("tr") },
      ],
    },
    {
      label: "背景透明度",
      submenu: BG_MENU_CHOICES.map((a) => ({
        label: a === 0 ? "纯透明（无背景）" : a + "%",
        type: "radio", checked: bg === a,
        click: () => applyBg(a),
      })),
      // menu keeps common tiers; the top-center slider is 1%-stepped 0..60
    },
    {
      label: "点击穿透", type: "checkbox", checked: passthrough,
      click: (i) => applyPassthrough(i.checked),
    },
    { type: "separator" },
    { label: "穿透时左上角按钮区仍可点/可右键", enabled: false },
    { type: "separator" },
    { label: "退出字幕窗", click: () => app.quit() },
  ];
  Menu.buildFromTemplate(template).popup({ window: win });
}

app.whenReady().then(() => {
  opts = parseArgs();
  lang = opts.lang;
  audioInput = opts.input; // before autoStartServer: serverSpawnArgs reads it
  autoStartServer(opts); // exe-as-bat: spawn server if not already up (async)

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

  ipcMain.on("hmy:show-menu", () => { diag("IPC: show-menu"); showMenu(); });
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

  win.webContents.on("did-finish-load", async () => {
    diag("did-finish-load URL=" + win.webContents.getURL());
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

  win.loadURL(buildUrl(opts.url, opts.show));

  pollTimer = setInterval(refreshMouseMode, 40);

  globalShortcut.register("Esc", () => app.quit());
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
  serverKillSync();
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (pollTimer) clearInterval(pollTimer);
});

app.on("window-all-closed", () => app.quit());