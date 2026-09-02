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
// Top-right window buttons: minimize (─) and close (✕), anchored to the right.
const RBX = 8;      // right-most button's right offset
const RBTN_W = BTN_S * 2 + BTN_GAP; // total width of the two right buttons

// Display mode: "both" = source + translation (bilingual stacked rows),
// "tr" = translation only (the source flow, incl. its in-progress draft, is
// hidden; the translation flow keeps showing).
const MODES = ["both", "tr"];
const MODE_LABEL = { both: "双语", tr: "译文" };

// Subtitle-card background opacity levels (percent; 0 = fully transparent,
// 30 = a soft black card). Text itself is ALWAYS opaque; only the backdrop
// behind the source+translation block changes. 5-step granularity powers
// both the settings-menu radios and the top-center slider.
const BG_CHOICES = Array.from({ length: 13 }, (_, i) => i * 5); // 0..60 step 5
// Top-center backdrop slider: 140px wide, centered on the window's top strip.
const SLIDER_W = 140;

// API/本地 translation channel toggle button sits to the RIGHT of the
// language button; the display-mode button sits next to it. The pass-through
// keep-clickable band counts 5 buttons now.
const NBUTTONS = 5;

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
    mode: "both", // "both" = bilingual rows, "tr" = translation only
    passthrough: false,
    bold: false, // bold subtitle text
    bg: 0, // subtitle-card backdrop opacity in percent (0 = fully transparent)
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
    else if (a === "--mode") {
      const v = args[++i] || "both";
      opts.mode = MODES.includes(v) ? v : "both";
    } else if (a === "--bold") opts.bold = true;
    else if (a === "--bg") {
      const v = parseInt(args[++i], 10);
      opts.bg = BG_CHOICES.includes(v) ? v : 0;
    } else if (a === "--show") {
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
let mode = "both";  // "both" = bilingual rows, "tr" = translation only
let bg = 0;         // subtitle-card backdrop opacity in percent
let bold = false;   // bold subtitle text (source + translation flows)
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
  const x0 = b.x + (b.width - SLIDER_W) / 2 - BAND_PAD;
  const x1 = x0 + SLIDER_W + BAND_PAD * 2;
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
  const lx4 = BTN_X0 + (BTN_S + BTN_GAP) * 4;
  // subtitle-card backdrop: only when bg > 0 does the block get a card
  const cardCss = opts.bg > 0
    ? `#box{background:rgba(0,0,0,${(opts.bg / 100).toFixed(2)})!important;
         border-radius:14px!important;padding:8px 14px!important;}
       #hmy-txt-flow,#hmy-tr-flow{margin:0;padding:0;}`
    : "";
  return `
    /* ACCUMULATING subtitle flow (desktop window owns ALL rendering):
       confirmed finals flow left-to-right / wrap on lines (top-anchored),
       and the in-progress partial draft rides INLINE at the end of the same
       text line -- exactly the current #final-line + #partial-line "same
       line" look. Each confirmed final segment keeps its own 8s lifetime and
       is removed when it expires. The same happens for translations below.
       The server's native overlay script still writes #final-line /
       #partial-line -- hide them; we render our own flow. */
    #box{top:44px!important;bottom:auto!important;text-align:left!important;
         max-width:100%;white-space:normal;box-sizing:border-box;}
    #final-line,#partial-line{display:none!important;}
    /* translation-only mode: hide the whole source flow (confirmed segments
       and its in-progress draft); only the translation flow stays visible */
    html.hmy-tr-only #hmy-txt-flow{display:none!important;}
    /* source-text flow: confirmed segments + trailing in-progress draft */
    #hmy-txt-flow{display:block;width:100%;box-sizing:border-box;
         font-size:${opts.size}px!important;color:#fff;
         text-shadow:0 0 8px #000,0 0 4px #000,2px 2px 2px #000;
         white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;
         line-height:1.35;}
    .hmy-txt-seg{display:inline;}
    .hmy-txt-part{display:inline;font-style:italic;opacity:0.9;}
    /* translation flow: confirmed per-segment translations + trailing draft */
    #hmy-tr-flow{display:block;width:100%;box-sizing:border-box;
         font-size:${opts.size}px!important;color:#ffd75e;
         text-shadow:0 0 6px #000,0 0 3px #000;
         white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;
         line-height:1.25;}
    .hmy-tr-seg{display:inline;}
    .hmy-tr-draft{display:inline;font-style:italic;}
    /* (fade-out removed: segments disappear immediately at lifetime end) */
    #box{font-size:${opts.size}px!important;}
    ${fontCss}
    /* OS-native drag while interactive */
    html.hmy-drag-mode body{position:fixed;top:0;right:0;bottom:0;left:0;
                            -webkit-app-region:drag;}
    html.hmy-drag-mode,html.hmy-drag-mode body{cursor:move;}
    /* buttons: no-drag => clickable even inside the drag region.
       Modern look: rounded, dark translucent, subtle hover lift. */
    .hmy-btn{position:fixed;top:${BTN_Y0}px;width:${BTN_S}px;height:${BTN_S}px;
             display:flex;align-items:center;justify-content:center;
             font-size:13px;line-height:1;background:rgba(0,0,0,0.55);
             color:#fff; /* white text on the dark button */
             border:1px solid rgba(255,255,255,0.30);border-radius:8px;
             cursor:pointer!important;user-select:none;
             -webkit-app-region:no-drag;z-index:2147483647;
             font-family:'Segoe UI Symbol','Segoe UI',sans-serif;
             transition:background .15s ease,border-color .15s ease,
                        transform .08s ease,box-shadow .15s ease;}
    #hmy-lock-btn{left:${lx0}px;}
    #hmy-menu-btn{left:${lx1}px;}
    #hmy-lang-btn{left:${lx2}px;font-family:'Segoe UI',sans-serif;font-weight:600;}
    #hmy-api-btn{left:${lx3}px;font-family:'Segoe UI',sans-serif;font-weight:600;
                 font-size:11px;}
    #hmy-mode-btn{left:${lx4}px;font-family:'Segoe UI',sans-serif;font-weight:600;
                  font-size:11px;}
    /* top-right window controls: minimize (─) and close (✕) */
    #hmy-min-btn{right:${RBX + BTN_S + BTN_GAP}px;}
    #hmy-close-btn{right:${RBX}px;}
    #hmy-close-btn:hover{background:rgba(220,60,60,0.85);border-color:rgba(255,120,120,0.6);}
    .hmy-btn:hover{background:rgba(65,65,75,0.9);border-color:rgba(255,255,255,0.6);
                   box-shadow:0 2px 8px rgba(0,0,0,0.45);}
    .hmy-btn:active{transform:translateY(1px);}
    #hmy-lock-btn.on{background:rgba(90,100,120,0.75);}
    .hmy-btn *{pointer-events:none;}
    /* top-center backdrop-opacity slider: thin track, round thumb, centered
       on the window's top strip between the left band and right controls */
    #hmy-bg-slider{position:fixed;top:${BTN_Y0 + (BTN_S - 10) / 2}px;
         left:50%;width:${SLIDER_W}px;margin-left:${-SLIDER_W / 2}px;
         height:10px;-webkit-appearance:none;appearance:none;background:transparent;
         cursor:pointer!important;user-select:none;-webkit-app-region:no-drag;
         z-index:2147483647;outline:none;}
    #hmy-bg-slider::-webkit-slider-runnable-track{height:4px;border-radius:2px;
         background:rgba(255,255,255,0.15);transition:background .15s ease;}
    #hmy-bg-slider::-webkit-slider-thumb{-webkit-appearance:none;width:10px;height:10px;
         border-radius:50%;background:rgba(255,255,255,0.55);margin-top:-3px;
         border:1px solid rgba(255,255,255,0.30);
         box-shadow:0 1px 3px rgba(0,0,0,0.4);
         transition:transform .08s ease,box-shadow .15s ease;}
    #hmy-bg-slider:hover::-webkit-slider-runnable-track{background:rgba(255,255,255,0.28);}
    #hmy-bg-slider:hover::-webkit-slider-thumb{background:rgba(255,255,255,0.85);}
    #hmy-bg-slider:active::-webkit-slider-thumb{transform:scale(1.15);}
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
    window.__hmyMode = 'both'; // 'both' = bilingual rows, 'tr' = translation only

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
    var modeBtn = mkBtn('hmy-mode-btn', '双语', '切换显示: 双语(原文+译文) / 仅译文');
    modeBtn.addEventListener('click', function(){
      console.log('hmy: btn-mode-click');
      window.desktopSubtitle.toggleMode();
    });
    var minBtn = mkBtn('hmy-min-btn', '─', '最小化窗口');
    minBtn.addEventListener('click', function(){
      console.log('hmy: btn-min-click');
      window.desktopSubtitle.minimize();
    });
    var closeBtn = mkBtn('hmy-close-btn', '✕', '退出字幕窗');
    closeBtn.addEventListener('click', function(){
      console.log('hmy: btn-close-click');
      window.desktopSubtitle.close();
    });
    var bgSlider = document.createElement('input');
    bgSlider.type = 'range';
    bgSlider.id = 'hmy-bg-slider';
    bgSlider.min = '0';
    bgSlider.max = '60';
    bgSlider.step = '5';
    bgSlider.value = '0'; // applyBg() syncs the real value right after init
    bgSlider.title = '背景透明度调节 (0-60%)';
    bgSlider.addEventListener('input', function(){
      console.log('hmy: bg-slider-input ' + bgSlider.value);
      window.desktopSubtitle.setBgAlpha(parseInt(bgSlider.value, 10));
    });
    document.body.appendChild(lock);
    document.body.appendChild(menuBtn);
    document.body.appendChild(langBtn);
    document.body.appendChild(apiBtn);
    document.body.appendChild(modeBtn);
    document.body.appendChild(minBtn);
    document.body.appendChild(closeBtn);
    document.body.appendChild(bgSlider);
    console.log('hmy: buttons injected');

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
      var h = box.offsetTop + box.offsetHeight + 16; // rows + bottom margin
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
        var md=document.getElementById('hmy-mode-btn');
        if(md){ md.textContent = ${JSON.stringify(MODE_LABEL[mode])}; }
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
  if (!BG_CHOICES.includes(alphaPct)) return;
  bg = alphaPct;
  const a = (bg / 100).toFixed(2);
  // The card spans the WHOLE top strip: it starts at the button row (top:2px,
  // buttons stay above it thanks to their fixed positioning + z-index) and
  // its padding-top keeps the text top just 8px below the buttons (2 + 40 =
  // 42; buttons end at 34). The flows get a one-line min-height (em units
  // follow the font size) so an EMPTY card keeps exactly the size of a
  // one-line card -- the bottom edge no longer shrinks above the text line.
  const css = bg > 0
    ? `#box{top:2px!important;padding:40px 14px 8px!important;
         background:rgba(0,0,0,${a})!important;border-radius:14px!important;}
       #hmy-txt-flow{min-height:1.35em!important;}
       #hmy-tr-flow{min-height:1.25em!important;}`
    : `#box{top:44px!important;padding:0!important;
         background:transparent!important;border-radius:0!important;}
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
      submenu: BG_CHOICES.map((a) => ({
        label: a === 0 ? "纯透明（无背景）" : a + "%",
        type: "radio", checked: bg === a,
        click: () => applyBg(a),
      })),
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
  // Default the CHANNEL to whatever the launcher does: the .bat starts the
  // server with --translate api:zh when a config exists, so the API channel
  // must be active in the UI from the start too -- otherwise the button says
  // 本地 while the server actually translates via the API (which accepts ANY
  // source language), which is exactly the "本地 mode translated non-Japanese"
  // confusion. Without a config, apiAvailable=false -> apiMode=false -> local.
  apiMode = apiAvailable;
  mode = MODES.includes(opts.mode) ? opts.mode : "both"; // (--mode arg if ever given)
  bg = BG_CHOICES.includes(opts.bg) ? opts.bg : 0;
  bold = !!opts.bold;

  const { workArea } = screen.getPrimaryDisplay();
  const winW = Math.min(opts.width, workArea.width);
  const winH = Math.min(opts.height, workArea.height);
  const winX = workArea.x + Math.floor((workArea.width - winW) / 2);
  const winY = workArea.y + workArea.height - winH;

  ipcMain.on("hmy:show-menu", () => { diag("IPC: show-menu"); showMenu(); });
  ipcMain.on("hmy:toggle-passthrough", () => { diag("IPC: toggle-passthrough"); applyPassthrough(!passthrough); });
  ipcMain.on("hmy:cycle-lang", () => { diag("IPC: cycle-lang"); cycleLang(); });
  ipcMain.on("hmy:cycle-api", () => { diag("IPC: cycle-api"); cycleApi(); });
  ipcMain.on("hmy:toggle-mode", () => { diag("IPC: toggle-mode"); applyMode(mode === "both" ? "tr" : "both"); });
  ipcMain.on("hmy:minimize", () => { diag("IPC: minimize"); if (win && !win.isDestroyed()) win.minimize(); });
  ipcMain.on("hmy:close", () => { diag("IPC: close"); app.quit(); });
  ipcMain.on("hmy:set-bg", (_e, v) => { diag("IPC: set-bg " + v); applyBg(parseInt(v, 10)); });
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
    try { applyMode(mode); } catch (e) { diag("applyMode err " + e.message); }
    try { applyBg(bg); } catch (e) { diag("applyBg err " + e.message); }
    try { applyBold(bold); } catch (e) { diag("applyBold err " + e.message); }
    try { setPageButtons(); } catch (e) { diag("setPageButtons err " + e.message); }
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

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (pollTimer) clearInterval(pollTimer);
});

app.on("window-all-closed", () => app.quit());