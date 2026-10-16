// Publish the packaged app FLAT into desktop-subtitle/ -- i.e. no
// dist/win-unpacked nesting, so the entry point is
// desktop-subtitle\早耳字幕.exe (one level below the project root).
//
// The exe locates the project root by walking UP from its own directory
// looking for scripts/realtime_transcribe.py + .venv/Scripts/python.exe, so a
// shallower location behaves exactly like the nested layout.
//
// Run by `npm run dist` (after electron-builder) or on its own via
// `npm run flat`. Idempotent: a manifest of the previously published entries
// (.published-flat.json) is used to remove stale artifacts first, and source
// files are never touched.
const fs = require("fs");
const path = require("path");

const appDir = path.resolve(__dirname, ".."); // desktop-subtitle/
const unpacked = path.join(appDir, "dist", "win-unpacked");
const manifest = path.join(appDir, ".published-flat.json");

// never publish over / delete these
const KEEP = new Set([
  "main.js", "preload.js", "package.json", "package-lock.json",
  ".gitignore", ".published-flat.json", "tools", "dist", "node_modules",
  "启动早耳.bat", "停止早耳.bat", "start_desktop_subtitle.ps1",
]);

const log = (m) => console.log("[publish-flat] " + m);

if (!fs.existsSync(unpacked)) {
  console.error("[publish-flat] missing " + unpacked + " -- run electron-builder first");
  process.exit(1);
}

let previous = [];
try {
  previous = JSON.parse(fs.readFileSync(manifest, "utf8"));
} catch (_) {
  previous = [];
}

// drop artifacts published by an earlier run (e.g. an exe with an older name)
for (const name of previous) {
  const target = path.join(appDir, name);
  if (!KEEP.has(name) && fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true });
    log("removed stale " + name);
  }
}

const entries = fs.readdirSync(unpacked);
for (const name of entries) {
  if (KEEP.has(name)) {
    log("skipped (source file): " + name);
    continue;
  }
  const src = path.join(unpacked, name);
  const dst = path.join(appDir, name);
  fs.rmSync(dst, { recursive: true, force: true });
  fs.cpSync(src, dst, { recursive: true });
  log("published " + name);
}

fs.writeFileSync(manifest, JSON.stringify(entries, null, 2) + "\n");
log("done: " + entries.length + " entries flat in " + appDir);