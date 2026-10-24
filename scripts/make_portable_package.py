"""Build the hayamimi portable package ("unzip and run") and its zip.

The package targets a machine with NO Python, NO Node and NO ffmpeg: unzip,
run 首次运行.bat once, then double-click the subtitle exe.

Layout produced (see docs/PORTABLE.md for the rationale):

    hayamimi/
      python/                    bundled CPython, copied from the venv's base
      .venv/                     runtime deps (首次运行.bat re-points pyvenv.cfg)
      models/                    full runtime model set
      scripts/ docs/ README* LICENSE THIRD_PARTY_NOTICES.md requirements.txt
      desktop-subtitle/          the packaged app FLAT (exe + dlls + locales/ +
                                 resources/) plus the two launcher .bat files
      首次运行.bat 使用说明.txt

The exe locates the project root by walking UP from its own directory looking
for scripts/realtime_transcribe.py + .venv/Scripts/python.exe, so the flat
layout (one level below the root) works exactly like the older
dist/win-unpacked/ nesting.

Usage:
    python scripts/make_portable_package.py                 # local dist zip
    python scripts/make_portable_package.py --app-zip PATH  # e.g. the release zip
    python scripts/make_portable_package.py --keep-stage    # keep the staged tree
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                # retired 2-class punctuation model (364MB,
                                # superseded by punct-ja-4class-permissive;
                                # nothing in the runtime references it anymore)
                                "mojicast-punct-onnx",
                                # huggingface_hub's download-cache dirs inside
                                # models fetched via download_hf_repo
                                ".cache")

USAGE_TXT = """hayamimi 整合包 {ver} —— 解压即用
=============================================

一、首次使用（解压后即可用）
1) 把整个文件夹解压到【短路径】下，例如  C:\\hayamimi
   （模型文件名较长，路径太深会触发 Windows 260 字符限制）
2) 双击  desktop-subtitle\\早耳字幕.exe
   → 出现字幕窗；exe 首次启动会自动把 .venv 指向包内 python\\，
      并在后台拉起服务器（端口 8833），日志：%TEMP%\\hayamimi-serve.log
3) （可选，推荐）双击一次  首次运行.bat
   做依赖自检 + 清除解压工具留下的“网络下载”标记（免除 SmartScreen 询问）；
   跳过它 exe 也能跑，只是少了上面两项

二、日常使用
- 直接用那个 exe 就是启动器：它探测 8833，没有就自己起服务器；关窗会停掉它启动的服务器
- 也可双击  desktop-subtitle\\启动早耳.bat （等价：先起服务器再起窗）；
  想一键全停就双击  desktop-subtitle\\停止早耳.bat
- 窗内按钮：图钉=标题栏自动隐藏开关 / 锁=点击穿透 / 齿轮=设置面板 /
  OFF·API=翻译语言与通道 / 麦克风图标=切换音频源（麦克风 → 系统音频 → 两者）
- 快捷键：Ctrl+Alt+S=设置面板 / Ctrl+Alt+B=标题栏常驻 / Ctrl+Alt+D=点击穿透 / Ctrl+Alt+L=语言
- 顶部两条滑块：字幕背景透明度、窗口宽度（只有按住圆点才能拖动，防误触）

三、音频源
- 麦克风：所有 Windows 都可用
- 系统音频 / 两者：走 WASAPI 回环（包内已含 soundcard），仅 Windows；
  切换会重启识别引擎，约 15–30 秒没有字幕

四、翻译
- 默认不翻译；语言按钮可切 EN/ZH/KO（本地模型），或切到 API 通道
- 本地模型已【全量】包含：ja/en/zh/ko/yue 识别、英/中翻译、中日标点、ITN 等
- 想用 API 翻译：把 openai_translate.example.json 复制成 openai_translate.json，
  填好 base_url / model（本包未附带真实配置，避免泄露 API Key）

五、不需要 / 未包含
- 不需要安装 Python、Node.js、ffmpeg
- 未包含 tests/ 与 demo/（开发用）
- 未包含 openai_translate.json（真实 API 配置）

六、卸载
- 直接删掉整个文件夹即可（日志只写 %TEMP%；包目录里唯一会被写入的是
  exe 首次启动时自动修正的 .venv\\pyvenv.cfg）

七、排错
- **窗口一片空白 / 显示错误页**：说明包不完整（缺 scripts\\、.venv\\、python\\ 或 models\\），
  或者你把 exe 单独拷出来运行了 → 请重新【完整解压】整个包，然后运行 首次运行.bat
  （新版遇到这种情况会直接在窗口里写明原因并弹对话框，不会再给个空白页）
- 双击 exe 出现“Windows 已保护你的电脑”（SmartScreen，程序未签名）：点【仍要运行】即可，
  同一文件只问一次；运行 首次运行.bat 也会自动清除这类“下载标记”；
  或改用 7-Zip / 资源管理器“解压到当前文件夹”，不要直接在 zip 里双击 exe
- 端口 8833 / 8766 被占用：先关掉已在运行的旧实例或占用进程
- 首次运行.bat 提示 “PACKAGE INCOMPLETE”：把整个包重新完整解压（别只拷 exe）
- 首次运行.bat 提示依赖检查失败：把窗口里的报错原样发给开发者
- 更多细节见包内 README.md、docs\\PORTABLE.md 等
"""

META_FILES = [
    "README.md", "README.en.md", "README.ja.md", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", "requirements.txt",
    "openai_translate.example.json", "首次运行.bat",
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def app_version() -> str:
    with open(ROOT / "desktop-subtitle" / "package.json", encoding="utf-8") as f:
        return json.load(f)["version"]


def venv_base_python() -> Path:
    """The interpreter the venv was built against (pyvenv.cfg `home`)."""
    cfg = ROOT / ".venv" / "pyvenv.cfg"
    for line in cfg.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("home"):
            home = line.split("=", 1)[1].strip()
            p = Path(home)
            if (p / "python.exe").exists():
                return p
            sys.exit(f"pyvenv.cfg home has no python.exe: {p}")
    sys.exit("pyvenv.cfg has no 'home' line")


def copytree(src: Path, dst: Path, label: str) -> None:
    t0 = time.time()
    shutil.copytree(src, dst, ignore=IGNORE, dirs_exist_ok=True)
    n = sum(len(f) for _, _, f in os.walk(dst))
    log(f"copied {label}: {n} files in {time.time() - t0:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ver = app_version()
    ap.add_argument("--app-zip", default=str(ROOT / "desktop-subtitle" / "dist" /
                                            f"hayamimi-subtitle-{ver}-win-x64.zip"),
                    help="packaged Electron zip (local dist build or the release asset)")
    ap.add_argument("--out", default=str(ROOT.parent / f"hayamimi-portable-{ver}.zip"))
    ap.add_argument("--stage", default=str(ROOT.parent / "_pkg" / "hayamimi"))
    ap.add_argument("--keep-stage", action="store_true",
                    help="keep the staged (unzipped) tree after zipping")
    args = ap.parse_args()

    app_zip = Path(args.app_zip)
    if not app_zip.exists():
        sys.exit(f"app zip not found: {app_zip}\n"
                 "Build it with `npm run dist` in desktop-subtitle/ or pass "
                 "--app-zip pointing at the release asset.")
    stage = Path(args.stage)
    out_zip = Path(args.out)

    if stage.exists():
        log(f"removing old stage {stage}")
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    copytree(venv_base_python(), stage / "python", "python (bundled interpreter)")
    copytree(ROOT / ".venv", stage / ".venv", ".venv")
    copytree(ROOT / "models", stage / "models", "models")
    copytree(ROOT / "scripts", stage / "scripts", "scripts")
    copytree(ROOT / "docs", stage / "docs", "docs")

    for name in META_FILES:
        src = ROOT / name
        if not src.exists():
            sys.exit(f"missing required file: {src}")
        shutil.copy2(src, stage / name)
    log("copied meta files")

    # desktop-subtitle: the packaged app FLAT + the launcher bats
    overlay = stage / "desktop-subtitle"
    overlay.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(app_zip) as zf:
        zf.extractall(overlay)
    exes = list(overlay.glob("*.exe"))
    if len(exes) != 1:
        sys.exit(f"expected exactly one .exe in the packaged app, found: {exes}")
    log(f"extracted packaged app flat: {sum(len(f) for _, _, f in os.walk(overlay))} entries, "
        f"exe={exes[0].name}")
    for name in ["启动早耳.bat", "停止早耳.bat"]:
        shutil.copy2(ROOT / "desktop-subtitle" / name, overlay / name)
    log("copied launcher bats")

    (stage / "使用说明.txt").write_text(USAGE_TXT.format(ver=ver), encoding="utf-8-sig")

    for bad in ["tests", "demo", ".github", "openai_translate.json",
                "desktop-subtitle/node_modules", ".git", "dist"]:
        if (stage / bad).exists():
            sys.exit(f"unexpected item in package: {bad}")
    for must in [stage / "python" / "python.exe",
                 stage / ".venv" / "Scripts" / "python.exe",
                 stage / "scripts" / "realtime_transcribe.py",
                 exes[0]]:
        if not must.exists():
            sys.exit(f"missing in package: {must}")

    total = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
    files = sum(1 for p in stage.rglob("*") if p.is_file())
    log(f"staged: {files} files, {total / 2**30:.2f} GiB")

    if out_zip.exists():
        os.remove(out_zip)
    log(f"zipping -> {out_zip}")
    t0 = time.time()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=1,
                         allowZip64=True) as zf:
        for p in stage.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(stage.parent).as_posix())
    log(f"zip done in {time.time() - t0:.1f}s: {out_zip.stat().st_size / 2**30:.2f} GiB")

    h = hashlib.sha256()
    with open(out_zip, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    log(f"sha256 {h.hexdigest()}")

    if not args.keep_stage:
        log(f"removing stage {stage} (pass --keep-stage to keep it)")
        shutil.rmtree(stage)
    log("DONE")


if __name__ == "__main__":
    main()