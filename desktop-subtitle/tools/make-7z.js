// Create the release archive as 7z (requested format) from the freshly
// built win-unpacked tree. Run by `npm run dist` after electron-builder and
// before publish-flat.
//
// electron-builder itself only emits zip; the zip stays as an internal
// artifact because scripts/make_portable_package.py defaults to it. The
// user-facing release asset is the .7z produced here.
//
// 7z is resolved from PATH (Windows ships NanaZip's 7z.exe alias, the
// official 7-Zip install also works). A missing 7z aborts loudly -- dist
// must not silently produce only the zip.
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const appDir = path.resolve(__dirname, ".."); // desktop-subtitle/
const pkg = JSON.parse(fs.readFileSync(path.join(appDir, "package.json"), "utf8"));
const unpacked = path.join(appDir, "dist", "win-unpacked");
const out = path.join(appDir, "dist", `hayamimi-subtitle-${pkg.version}-win-x64.7z`);

if (!fs.existsSync(unpacked)) {
  console.error("[make-7z] missing " + unpacked + " -- run electron-builder first");
  process.exit(1);
}

fs.rmSync(out, { force: true });
console.log("[make-7z] compressing " + unpacked + " -> " + out);
const r = spawnSync("7z", ["a", "-t7z", "-mx=9", "-y", out, path.join(unpacked, "*")], {
  stdio: "inherit",
  shell: false,
});
if (r.error) {
  console.error("[make-7z] cannot run 7z (NanaZip/7-Zip not found in PATH): " + r.error.message);
  process.exit(1);
}
if (r.status !== 0) {
  console.error("[make-7z] 7z exited with code " + r.status);
  process.exit(r.status || 1);
}
const size = (fs.statSync(out).size / 1048576).toFixed(1);
console.log("[make-7z] done: " + out + " (" + size + " MB)");