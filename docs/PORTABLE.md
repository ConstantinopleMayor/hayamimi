# Portable package (unzip-and-run)

A single self-contained folder for machines that have **no Python, no Node and
no ffmpeg** installed: unzip, double-click the subtitle exe. `首次运行.bat` is
optional (dependency check + download-mark cleanup); the exe self-heals `.venv`
by itself. Built and verified on 2026-09-16, re-verified 2026-10-17 (see
"Verification" below).

## Layout

```
hayamimi/
  python/                      bundled CPython 3.12.13 (portable, ~52 MiB)
  .venv/                       runtime dependencies (~609 MiB)
  models/                      FULL runtime model set (~3.1 GiB)
  scripts/  docs/  README*  LICENSE  THIRD_PARTY_NOTICES.md  requirements.txt
  desktop-subtitle/            the packaged app FLAT: the exe, its dlls,
                                locales/ and resources/ sit directly here
                                (no dist/win-unpacked nesting)
    早耳字幕.exe                  the exe is the launcher
    启动早耳.bat / 停止早耳.bat   portable launchers (paths derived from %~dp0)
  首次运行.bat                   optional first-run setup (checks deps,
                                clears download marks; the exe self-heals .venv)
  使用说明.txt                   end-user notes (zh)
```

The flat layout works because the exe only walks UP looking for the project
root (see below) -- it does not care how deep it sits. `启动早耳.bat` and
`start_desktop_subtitle.ps1` look for the packaged exe in both shapes
(`desktop-subtitle\*.exe`, then the legacy `desktop-subtitle\dist\win-unpacked\*.exe`).

The exe doubles as the launcher: it probes `127.0.0.1:8833`, walks up from its
own directory to find `scripts/realtime_transcribe.py` + `.venv/Scripts/python.exe`
(`main.js` `findProjectRoot`), and spawns the server with `cwd = project root`.
Before spawning it **self-heals `.venv/pyvenv.cfg`** whenever the package
carries its own `python\` folder (same four lines as `首次运行.bat`), so
skipping the one-time setup still works. Logs go to `%TEMP%\hayamimi-serve.log`
/ `hayamimi-desktop-subtitle.log`, so the self-heal is the only thing ever
written inside the package -- deleting the folder removes everything.

## Why the venv is portable

A venv normally breaks when moved because `pyvenv.cfg` records an absolute
`home`. A full scan of `.venv` (`*.pth`, `*.cfg`, `*.egg-link`) found **no
other absolute path**, so `首次运行.bat` rewrites four lines (the exe runs the same self-heal at
startup when it finds `python\` next to the root):

```
home = <package>\python
executable = <package>\python\python.exe
include-system-site-packages = false
version = 3.12.13
```

The venv's `python.exe` is a redirector that reads that file, so the package's
own interpreter is used and nothing outside the folder is consulted.

## Items deliberately excluded

| Excluded | Why |
|---|---|
| `openai_translate.json` | may hold an API key -- only `openai_translate.example.json` ships |
| `openai_translate.json` / `tests/` / `demo/` / `.github/` | not needed at runtime (dev-only) |
| `node_modules/` | the packaged exe is self-contained |
| ffmpeg | only the dataset/eval scripts (`make_realset*.py`) use it -- never the live pipeline |
| `sherpa-onnx-zipformer-korean-2024-06-24` | `download_models.py` labels it "eval baseline only"; ko runs on SenseVoice (`asr_engine.py` SV_LANGS) |

## Building / refreshing the package

`scripts/make_portable_package.py` does the whole job (committed, so a rebuild
is one command):

```bash
cd desktop-subtitle && npm run dist    # produce the app zip (or use the release asset)
cd .. && python scripts/make_portable_package.py
#   --app-zip PATH   point at a downloaded release zip instead of a local build
#   --keep-stage     keep the unzipped staging tree
```

It copies the bundled interpreter (auto-detected from `.venv/pyvenv.cfg`'s
`home`), the repo `.venv`, the full `models/`, `scripts/`, `docs/` and the root
meta files; extracts the app zip **flat** into `desktop-subtitle/`; adds
`首次运行.bat` + `使用说明.txt`; verifies nothing dev-only slipped in; then
writes `hayamimi-portable-<version>.zip` next to the repo and prints its size
and SHA-256. No 7-Zip is required (`zipfile` with Zip64); the int8 ONNX models
barely compress (~4.06 GiB -> ~2.95 GiB).

## Verification (2026-09-16, v0.3.2, flat layout; superseded by the 2026-10-17 re-verification below)

Built: `D:\AGI\hayamimi-portable-0.3.2.zip`
`2.95 GiB`, SHA-256 `f2c9564ff5bb854e9abd1698d53a3d8cdb55bae3f412c381052a4d89a7a3f32a`
(staged tree: 6560 files, 4.06 GiB). An earlier build of the same version used
the nested `dist/win-unpacked/` layout (SHA `c47cdf26...`); the flat one above
supersedes it.

Extracted to `C:\hayamimi-test` (a stand-in for a fresh machine) and:

1. `zipfile.testzip()` -> `None` (archive intact), including the non-ASCII
   `早耳字幕.exe` entry; the exe lands in `desktop-subtitle/` directly and no
   `dist/win-unpacked` exists.
2. `首次运行.bat` -> rewrote `pyvenv.cfg`; dependency self-check reported
   `python 3.12.13`, `prefix C:\hayamimi-test\hayamimi\.venv`,
   `base C:\hayamimi-test\hayamimi\python`, `OK soundcard`, `OK kiwipiepy`.
3. Self-containment: `sys.base_prefix` = the package's `python`,
   `sys.executable` = the package's venv python, and **no `sys.path` entry
   points outside the package** (no `D:` paths).
4. Launched the packaged exe: it found the project root and spawned
   `C:\hayamimi-test\hayamimi\.venv\Scripts\python.exe scripts\realtime_transcribe.py --serve 8833`
   (which re-execs the package's `python\python.exe`); `http://localhost:8833/`
   answered **HTTP 200**; the window logged `did-finish-load` -> `bootstrap done`
   with no errors; the server process grew to **2.5 GiB** working set, i.e. the
   ASR models really loaded from the package's `models/`.

Still worth an end-user check (needs a microphone): speak and confirm subtitles
appear, switch the language button to ZH (local MT loads on first use), and try
the speaker/mix audio source (restarts the engine, ~15-30 s).

### Re-verification 2026-10-17 (archive rebuilt with the startup-flow fix)

Cold-start testing exposed a UX bug in the 09-16 build: the window loaded the
overlay URL immediately, i.e. before the freshly spawned server had bound 8833.
Result: a false "服务器未启动" dialog a few seconds in, and a Chromium error
page that never recovered even though the server came up later. The exe now
probes/spawns first, shows a `正在启动字幕服务器…` page, and swaps to the
overlay when 8833 answers. `main.js` also self-heals `.venv\\pyvenv.cfg` (same
four lines as `首次运行.bat`) whenever the package carries its own `python\\`,
so the one-time setup is optional. Re-verified on freshly extracted copies:

1. `首次运行.bat` -> preflight OK, deps OK; exe -> warm-up page -> overlay,
   HTTP 200, no `did-fail-load` (no self-heal needed: the .bat's cfg already
   points at the bundled python).
2. Cold start **without** `首次运行.bat` -> `pyvenv.cfg self-healed -> <pkg>\\python`
   -> warm-up page -> overlay -> HTTP 200, live subtitles confirmed.
3. pyvenv.cfg pointed at a nonexistent python (fresh-machine simulation) ->
   self-heal again -> working window, **no** dialog.
4. exe copied out of any package -> the readable "找不到项目目录" notice page
   loads (this path previously never actually rendered) + one dialog.
5. Dev checkout launch (no `python\\` folder) -> no self-heal, normal start.

## Caveats for the receiving machine

- Windows 10/11 x64 only; unzip to a **short path** (e.g. `C:\hayamimi`) --
  model filenames are long and the 260-character limit can bite.
- SmartScreen will warn on first launch (unsigned): click **Run anyway** -- the
  same file is only asked about once, and `首次运行.bat` also clears any
  "downloaded from the internet" marks (`Unblock-File`). Never run the exe from
  inside the zip (Explorer extracts it to a temp folder where the project root
  is missing); extract the whole archive first.
- Ports 8833 (HTTP/SSE) and 8766 (WebSocket ingest) must be free.
- Extracting a ~3 GiB zip with Explorer is slow; 7-Zip/WinRAR is faster.

## Troubleshooting a blank window

A missing `scripts/`, `.venv/`, `python/` or `models/` used to leave a blank
Chromium error page, which reads as "nothing happened". The exe now detects
both cases and explains itself:

| Symptom | Meaning | What the exe does now |
|---|---|---|
| `project root not found near exe` in `%TEMP%\hayamimi-desktop-subtitle.log` | the exe is not inside a complete package (or was copied out alone) | loads a readable notice page (project root, missing pieces, how to fix) **and** raises a dialog |
| window shows `正在启动字幕服务器…` | normal warm-up: a freshly spawned server loads its models for 15-30 s | swaps to the overlay automatically once 8833 answers (no dialog) |
| the overlay URL fails to load (e.g. `did-fail-load`, port 8833 taken) | the server is not running (and was not just spawned) | logs `did-fail-load ...` and raises a one-time dialog pointing at `%TEMP%\hayamimi-serve.log` / `启动早耳.bat` |
| `首次运行.bat` prints `PACKAGE INCOMPLETE - missing: scripts\ ...` | the archive was not fully extracted | stops right there instead of "succeeding" into a broken setup |

The diag log also records `execPath`, `launchDir` and the **resolved project
root**, so a support request can be answered from the log alone.

## This repository's own desktop-subtitle folder is flat too

`npm run dist` now ends with `tools/publish-flat.js`, which copies
`dist/win-unpacked/*` into `desktop-subtitle/` itself, so the dev checkout's
entry point is also `desktop-subtitle\早耳字幕.exe`. `*.exe/dll/pak/bin/dat`,
`locales/`, `resources/` and the other app artifacts are gitignored there; only
`main.js`, `preload.js`, `package.json`, `tools/` and the launcher scripts are
tracked. Use `npm run flat` to re-publish without rebuilding.