# Portable package (unzip-and-run)

A single self-contained folder for machines that have **no Python, no Node and
no ffmpeg** installed: unzip, run `首次运行.bat` once, double-click the subtitle
exe. Built and verified on 2026-09-16 (see "Verification" below).

## Layout

```
hayamimi/
  python/                      bundled CPython 3.12.13 (portable, ~52 MiB)
  .venv/                       runtime dependencies (~609 MiB)
  models/                      FULL runtime model set (~3.1 GiB)
  scripts/  docs/  README*  LICENSE  THIRD_PARTY_NOTICES.md  requirements.txt
  desktop-subtitle/
    dist/win-unpacked/         packaged Electron app (the exe is the launcher)
    启动早耳.bat / 停止早耳.bat   portable launchers (paths derived from %~dp0)
  首次运行.bat                   first-run setup (re-points .venv, checks deps)
  使用说明.txt                   end-user notes (zh)
```

The exe doubles as the launcher: it probes `127.0.0.1:8833`, walks up from its
own directory to find `scripts/realtime_transcribe.py` + `.venv/Scripts/python.exe`
(`main.js` `findProjectRoot`), and spawns the server with `cwd = project root`.
Logs go to `%TEMP%\hayamimi-serve.log` / `hayamimi-desktop-subtitle.log`, so
nothing is written inside the package directory -- deleting the folder removes
everything.

## Why the venv is portable

A venv normally breaks when moved because `pyvenv.cfg` records an absolute
`home`. A full scan of `.venv` (`*.pth`, `*.cfg`, `*.egg-link`) found **no
other absolute path**, so `首次运行.bat` rewrites four lines:

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

1. Make sure the packaged app zip for the target version exists (CI builds it on
   the tag; it can also come from a local `npm run dist` in `desktop-subtitle/`).
2. Run the packaging script (staging copy + extraction + zip), which:
   copies `python/` (the uv standalone CPython), the repo `.venv`, the full
   `models/`, `scripts/`, `docs/` and the root meta files; extracts the app zip
   into `desktop-subtitle/dist/win-unpacked/`; adds `首次运行.bat` +
   `使用说明.txt`; then zips the stage as `hayamimi-portable-<version>.zip`.
3. No 7-Zip is required: `zipfile` (Zip64) or the bundled `tar.exe` work. The
   models are int8 ONNX, which barely compresses.

## Verification (2026-09-16, v0.3.2)

Built: `D:\AGI\hayamimi-portable-0.3.2.zip`
`2.95 GiB`, SHA-256 `c47cdf26960e84b98a9d91530727048a38056c90f7b962fe4600c5a7956fe908`
(staged tree: 6558 files, 4.06 GiB).

Extracted to `C:\hayamimi-test` (a stand-in for a fresh machine) and:

1. `zipfile.testzip()` -> `None` (archive intact), including the non-ASCII
   `早耳字幕.exe` entry.
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

## Caveats for the receiving machine

- Windows 10/11 x64 only; unzip to a **short path** (e.g. `C:\hayamimi`) --
  model filenames are long and the 260-character limit can bite.
- SmartScreen will warn on first launch (unsigned): "More info" -> "Run anyway".
- Ports 8833 (HTTP/SSE) and 8766 (WebSocket ingest) must be free.
- Extracting a ~3 GiB zip with Explorer is slow; 7-Zip/WinRAR is faster.