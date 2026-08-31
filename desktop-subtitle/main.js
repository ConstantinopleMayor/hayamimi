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

const DIAG = path.join(__dirname, "diag.log");
let diagOpen = false;
function diag(msg) {
  try {
    if (!diagOpen) { diagOpen = true; fs.writeFileSync(DIAG, ""); }
    fs.appendFileSync(DIAG, new Date().toISOString() + " " + msg + "\n");
  } catch (_) {}
}

const DEFAULT_SIZE = 20;  // 20pt initial subtitle size
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

// Button band geometry (top-left of the window). Same numbers in CSS.
const BTN_X0 = 8;   // first button left
const BTN_Y0 = 8;   // top
const BTN_S = 26;   // button size
const BTN_GAP = 6;  // gap between buttons
const BAND_PAD = 6; // pass-through keep-clickable band padding

// API/本地 translation channel toggle button sits to the RIGHT of the
// language button. The pass-through keep-clickable band counts 4 buttons now.
const NBUTTONS = 4;

// True when a usable openai_translate.json exists next to this app (i.e. the
// server can serve `api:` targets). Keeps the API button inert otherwise.
let apiAvailable = false;
// False = language button sends plain specs (local MT: zh/en/ko).
// True  = language button sends api: specs (OpenAI-compatible endpoint).
let apiMode = false;

// The server reads the same file (scripts/../openai_translate.json), so this
// detection mirrors translate_api.load_config()'s usability rule: base_url
// and model must be non-empty for the api: route to be usable.
function detectApiConfig() {
  try {
    const p = path.join(__dirname, "..", "openai_translate.json");
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
    passthrough: false,
    size: DEFAULT_SIZE,
    font: "",
    lang: "zh", // match the .bat default translation (--translate zh)
  };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--width") opts.width = parseInt(args[++i], 10) || opts.width;
    else if (a === "--height") opts.height = parseInt(args[++i], 10) || opts.height;
    else if (a === "--size") opts.size = parseInt(args[++i], 10) || DEFAULT_SIZE;
    else if (a === "--font") opts.font = args[++i] || "";
    else if (a === "--lang") opts.lang = args[++i] || "zh";
    else if (a === "--show") {
      const v = args[++i] || "both";
      if (["final", "partial", "both"].includes(v)) opts.show = v;
    } else if (a === "--passthrough") opts.passthrough = true;
    else if (a === "--url") opts.url = args[++i] || opts.url;
  }
  if (!LANGS.includes(opts.lang)) opts.lang = "zh";
  return opts;
}

function buildUrl(base, show) {
  if (!show || show === "both") return base;
  return base + (base.includes("?") ? "&" : "?") + "show=" + show;
}

let win = null;
let opts = null;
let passthrough = false;
let lang = "zh"; // default matches the .bat (--translate zh)
let sizeKey = null;
let fontKey = null;
let pollTimer = null;

// pass-through polling: in click-through mode keep exactly the top-left button
// band interactive; everything else clicks through.
function isCursorInButtonBand() {
  if (!win || win.isDestroyed()) return false;
  const c = screen.getCursorScreenPoint();
  const b = win.getBounds();
  const nx = (b.x + c.x >= 0) ? c.x : 0; // c.x is absolute screen coord
  const bandX2 = b.x + BTN_X0 + BTN_S * NBUTTONS + BTN_GAP * (NBUTTONS - 1) + BAND_PAD;
  const bandY2 = b.y + BTN_Y0 + BTN_S + BAND_PAD;
  return c.x >= b.x && c.x <= bandX2 && c.y >= b.y && c.y <= bandY2;
}

function refreshMouseMode() {
  if (!win || win.isDestroyed()) return;
  if (!passthrough) win.setIgnoreMouseEvents(false);
  else win.setIgnoreMouseEvents(!isCursorInButtonBand(), { forward: true });
}

function makeCss() {
  const fontCss = opts.font ? `#box{font-family:${opts.font}!important;}` : "";
  const lx0 = BTN_X0;
  const lx1 = BTN_X0 + BTN_S + BTN_GAP;
  const lx2 = BTN_X0 + (BTN_S + BTN_GAP) * 2;
  const lx3 = BTN_X0 + (BTN_S + BTN_GAP) * 3;
  return `
    /* subtitles flow TOP -> BOTTOM, anchored to the top so the confirmed line   */
    /* is never clipped. Plain INLINE flow (NOT flex!): #final-line and        */
    /* #partial-line are inline spans, so the partial ALWAYS follows the       */
    /* final on the same line, wrapping together naturally -- it can never be  */
    /* pushed to its own row like flex items can. #hmy-tr is a block, so the   */
    /* translation always sits on its OWN line below.                         */
    #box{top:44px!important;bottom:auto!important;text-align:left!important;
         max-width:100%;white-space:normal;}
    #final-line,#partial-line{display:inline!important;max-width:100%;
         white-space:pre-wrap;overflow-wrap:anywhere;
         min-height:0!important;text-align:left!important;}
    /* in-progress (partial) subtitle uses the SAME size as the confirmed
       (final) line — the server's overlay CSS shrinks partial to 0.8em,
       which makes it bob around as it grows; force it to 1em (inherit
       #box's font-size) so size stays stable while typing. */
    #partial-line{font-size:1em!important;opacity:0.9;}
    #hmy-tr{display:block;margin-top:0.3em;max-width:100%;
            font-size:${opts.size}px!important;opacity:0.92;color:#ffd75e;
            text-shadow:0 0 6px #000,0 0 3px #000;min-height:1.1em;text-align:left;
            white-space:pre-wrap;overflow-wrap:anywhere;}
    #box{font-size:${opts.size}px!important;}
    ${fontCss}
    /* OS-native drag while interactive */
    html.hmy-drag-mode body{position:fixed;top:0;right:0;bottom:0;left:0;
                            -webkit-app-region:drag;}
    html.hmy-drag-mode,html.hmy-drag-mode body{cursor:move;}
    /* buttons: no-drag => clickable even inside the drag region */
    .hmy-btn{position:fixed;top:${BTN_Y0}px;width:${BTN_S}px;height:${BTN_S}px;
             display:flex;align-items:center;justify-content:center;
             font-size:13px;line-height:1;background:rgba(0,0,0,0.60);
             color:#fff; /* white text on the dark button */
             border:1px solid rgba(255,255,255,0.35);border-radius:6px;
             cursor:pointer!important;user-select:none;
             -webkit-app-region:no-drag;z-index:2147483647;}
    #hmy-lock-btn{left:${lx0}px;}
    #hmy-menu-btn{left:${lx1}px;}
    #hmy-lang-btn{left:${lx2}px;font-family:'Segoe UI',sans-serif;font-weight:600;}
    #hmy-api-btn{left:${lx3}px;font-family:'Segoe UI',sans-serif;font-weight:600;
                 font-size:11px;}
    .hmy-btn:hover{background:rgba(40,40,40,0.85);}
    #hmy-lock-btn.on{background:rgba(90,100,120,0.7);}
    .hmy-btn *{pointer-events:none;}
    /* API channel unavailable (no openai_translate.json): grey, inert */
    .hmy-btn.off{opacity:0.45;background:rgba(60,60,60,0.5);
                 border-color:rgba(255,255,255,0.15);cursor:default!important;}
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

    function mkBtn(id, text, title){
      var d = document.createElement('div');
      d.className = 'hmy-btn';
      d.id = id;
      d.textContent = text;
      d.title = title;
      return d;
    }
    var lock = mkBtn('hmy-lock-btn', '🔒', '点击切换穿透');
    lock.addEventListener('click', function(){
      console.log('hmy: btn-lock-click');
      window.desktopSubtitle.togglePassthrough();
    });
    var menuBtn = mkBtn('hmy-menu-btn', '⚙', '打开设置菜单');
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
    document.body.appendChild(lock);
    document.body.appendChild(menuBtn);
    document.body.appendChild(langBtn);
    document.body.appendChild(apiBtn);
    console.log('hmy: buttons injected');

    // translation line under the subtitle
    var box = document.getElementById('box');
    if (box) {
      var tr = document.createElement('div');
      tr.id = 'hmy-tr';
      tr.textContent = '';
      box.appendChild(tr);
    }
    window.__hmyShowTr = function(){
      var el = document.getElementById('hmy-tr');
      if (el) el.textContent = window.__hmyTr[window.__hmyLang] || '';
    };

    // --- height auto-fit: every 500ms measure how tall #box (subtitle +
    // translation rows) actually is and ask the window to grow/shrink to fit.
    // Width stays fixed -- Chinese/any text wraps instead of overflowing.
    // Polling only sets the height; it never reacts to the window height, so
    // there is no feedback loop (unlike the removed MutationObserver code).
    function fitHeight() {
      var box = document.getElementById('box');
      if (!box) return;
      var h = box.offsetTop + box.offsetHeight + 16; // rows + bottom margin
      window.desktopSubtitle.resize(0, h);
    }
    setInterval(fitHeight, 500);

    // API translation is slow (1-5s), so the translation MUST NOT be cleared
    // when a new final arrives -- otherwise it flickers out before the new
    // line's translation shows up. Instead the translation line keeps the
    // latest completed translation and only gets REPLACED when the next
    // translation event arrives. seq is intentionally ignored here: dropping
    // a slow translation because a newer final appeared was the "translation
    // disappears" bug. The displayed translation lags the subtitle by design
    // for API mode; local MT is fast enough that lag is invisible.
    var es = new EventSource('/events');
    es.onmessage = function(e){
      var ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (ev.type === 'translation') {
        window.__hmyTr[ev.lang] = ev.text;   // latest per-language translation
        if (window.__hmyLang === ev.lang) {  // render the active language
          var el = document.getElementById('hmy-tr');
          if (el) el.textContent = ev.text;  // replace, never clear
        }
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
  // Icon semantics: 🔒 = window is LOCKED in place (click-through/passthrough,
  // cannot be dragged) — 🔓 = unlocked, can drag/move the window.
  // (Previously it was inverted: passthrough showed 🔓 which was confusing.)
  const icon = passthrough ? "🔒" : "🔓";
  const onClass = passthrough ? " on" : "";
  const label = LANG_LABEL[lang] || "EN";
  win.webContents
    .executeJavaScript(
      `(function(){
        var lock=document.getElementById('hmy-lock-btn');
        if(lock){ lock.textContent=${JSON.stringify(icon)}; lock.className='hmy-btn${onClass}'; }
        var lp=document.getElementById('hmy-lang-btn');
        if(lp){ lp.textContent=${JSON.stringify(label)}; }
        var ap=document.getElementById('hmy-api-btn');
        if(ap){
          var apiLabel = ${JSON.stringify(apiAvailable ? (apiMode ? 'API' : '本地') : '—')};
          ap.textContent = apiLabel;
          ap.className = 'hmy-btn' + (${JSON.stringify(apiAvailable ? '' : ' off')});
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

function applyFontSize(px) {
  opts.size = px;
  // webContents.insertCSS() returns a Promise<CSSKey>; storing the promise
  // itself and later calling removeInsertedCSS(promise) throws
  // "Failed to serialize arguments". Fix: never remove -- just insert a NEW
  // rule. CSS is cascade-layered: a later-inserted rule with the same
  // specificity and !important wins over the earlier one, so re-inserting is
  // both simpler and always applies the newest size (no async race).
  const css = `#box{font-size:${px}px!important;} #final-line,#partial-line{font-size:${px}px!important;} #hmy-tr{font-size:${px}px!important;}`;
  win.webContents.insertCSS(css).catch(() => {});
}

function applyFont(family) {
  opts.font = family;
  const css = family ? `#box{font-family:${family}!important;} #final-line,#partial-line,#hmy-tr{font-family:${family}!important;}` : "";
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

  // API-translation availability: a usable openai_translate.json in the
  // project root (one directory above this app) enables the "API" channel
  // button; without it the button stays greyed/local and behavior is the
  // pre-API version.
  apiAvailable = detectApiConfig();
  if (apiAvailable) { diag("api config present: API channel enabled"); }
  else { diag("no usable openai_translate.json: API channel disabled"); }

  const { workArea } = screen.getPrimaryDisplay();
  const winW = Math.min(opts.width, workArea.width);
  const winH = Math.min(opts.height, workArea.height);
  const winX = workArea.x + Math.floor((workArea.width - winW) / 2);
  const winY = workArea.y + workArea.height - winH;

  ipcMain.on("hmy:show-menu", () => { diag("IPC: show-menu"); showMenu(); });
  ipcMain.on("hmy:toggle-passthrough", () => { diag("IPC: toggle-passthrough"); applyPassthrough(!passthrough); });
  ipcMain.on("hmy:cycle-lang", () => { diag("IPC: cycle-lang"); cycleLang(); });
  ipcMain.on("hmy:cycle-api", () => { diag("IPC: cycle-api"); cycleApi(); });
  // Height-only auto-fit: the window WIDTH stays fixed (text wraps instead),
  // the HEIGHT follows the subtitle+translation content. The renderer polls
  // every 500ms, and we debounce with a threshold so this is strictly one-way
  // (no resize<->observer feedback loop -- unlike the removed MutationObserver
  // version this cannot deadlock).
  let lastAutoH = 0;
  ipcMain.on("hmy:resize", (_e, w, h) => {
    if (!win || win.isDestroyed()) return;
    h = Math.max(opts.height, Math.min(Math.round(h), workArea.height));
    if (Math.abs(h - lastAutoH) < 4) return; // debounce: ignore tiny jitters
    lastAutoH = h;
    const [curW] = win.getSize();
    win.setSize(curW, h); // width unchanged
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
    resizable: false,
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
    try { setPageButtons(); } catch (e) { diag("setPageButtons err " + e.message); }
    diag("bootstrap done");
  });

  win.loadURL(buildUrl(opts.url, opts.show));

  pollTimer = setInterval(refreshMouseMode, 40);

  globalShortcut.register("Esc", () => app.quit());
  globalShortcut.register("Control+Alt+D", () => applyPassthrough(!passthrough));
  globalShortcut.register("Control+Alt+L", () => cycleLang());
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (pollTimer) clearInterval(pollTimer);
});

app.on("window-all-closed", () => app.quit());