# Session Notes

> 最近更新：2026-11-01  
> 用途：上下文压缩/新会话恢复时先读本文件与 `docs/PLAN_SEMANTIC_BOUNDARY.md`。

## Objective

完成并验证按 ASR 来源区分的语义断句策略，保持 `lookahead=1.0`、移除音频裁剪，并确认：

- `pz/rz` 使用文本 tail；
- `sv/v2/v3` native punctuation，直接 final；
- `omni` 禁用 live semantic split；
- `test11`、`test12` 不再出现错误切分/漏词。

## Important Details

- 项目根目录：`D:\AGI\hayamimi`
- 计划文档：`docs/PLAN_SEMANTIC_BOUNDARY.md`
- 历史排查记录：`docs/HANDOFF_test11_split.md`（已被计划文档取代，保留作证据）
- 当前分支：`main`
- 最近提交：
  ```text
  147ce88 fix(subtitle): remove audio trimming from semantic flush
  58b71c1 fix(subtitle): stabilize semantic boundaries and sentence finals
  02de09a docs: fix 0.4.0 subtitle window usage notes
  ```
- 当前工作区有未提交的 source-aware policy：
  ```text
  M scripts/asr_engine.py
  M scripts/realtime_transcribe.py
  M tests/test_units.py
  ```
- 新文档未跟踪：
  ```text
  ?? docs/HANDOFF_test11_split.md
  ?? docs/PLAN_SEMANTIC_BOUNDARY.md
  ?? docs/SESSION_NOTES.md
  ```
- 不提交、不推送，除非用户明确要求。
- 测试命令：
  ```powershell
  & '.\.venv\Scripts\python.exe' -m pytest -q
  ```
- 当前结果：
  ```text
  236 passed, 2 skipped, 5 warnings
  ```
- 两个 skipped：
  - `test_overlay_parity.py::test_overlay_html_matches_mobile_port`：缺少 `mobile/.../overlay_html.dart`
  - `test_lifecycle.py::test_run_clip_leaves_no_engine_behind`：缺少 `tests/golden/ja/*.wav`
- 5 warnings 来自 `download_models.py:147` 的 `tarfile.extract` Python 3.14 DeprecationWarning。
- 核心针对性测试：
  ```text
  27 passed, 94 deselected
  ```
- serve：
  ```text
  python scripts/realtime_transcribe.py --serve 8833 --input speaker --mode single --model pz
  ```
  `http://127.0.0.1:8833/` 返回 200。

## Work State

### Completed

- 移除 `trim_if_flushed()` 和全部 audio trim 常量/测试。
- 保留 `SPLIT_END_TOLERANCE=1`，修复 `vad.flush()` off-by-one。
- `test11`：`如何。` / `所有内容。` 正常。
- `test12`：`他认为，在此之前需要改变……` 正常。
- 实现 `RoutedASR.last_partial_source`。
- 实现 source-aware `BOUNDARY_POLICIES`：
  - pz：8/20；
  - rz/sv：period 8/20，strong 12/24；
  - v2/v3：仅 Latin 20/24；
  - omni：`allow_live=False`。
- `sv` 保留 semantic flush、无文本 tail、直接 final。
- `v2/v3` native direct final，不使用 CJK 分支。
- `omni` `live_disabled` 生效，`flush=0`。
- 完整 pytest 与 targeted pytest 通过。
- 新增 `docs/PLAN_SEMANTIC_BOUNDARY.md` 和本文件。

### Active

- 审查并准备提交 source-aware policy（尚未 commit）。
- 决定是否需要针对 `rz` 日语、`sv` 韩文做进一步阈值采样。
- `sv` 在 `测试11` 的 native direct final 会出现：
  ```text
  ...今天视频的所有。
  内容？
  ```
  这是 `sv` 不做 tail 的预期结果，不是 pz 回归。

### Blocked

- 无代码阻塞。
- `sv` 韩文 Hangul 目前进入 Latin 分支，是否要单独加 Hangul 阈值仍待决定。

## Next Move

1. `git diff` 审查 `scripts/asr_engine.py`、`scripts/realtime_transcribe.py`、`tests/test_units.py`。
2. 确认策略后提交本地 Git（用户明确要求时）。
3. 如需更细调，使用 `rz`/`sv` 代表性音频观察 `stale_tail`、`flush`、final 边界。
4. 更新计划/会话文档中的提交哈希和最终状态。

## Relevant Files

- `docs/PLAN_SEMANTIC_BOUNDARY.md`：完整设计与策略。
- `docs/HANDOFF_test11_split.md`：早期 root-cause/测试记录。
- `scripts/asr_engine.py`：partial source 记录。
- `scripts/realtime_transcribe.py`：`BoundaryPolicy`、`should_flush(source=...)`、`_NO_TAIL_SOURCES`。
- `tests/test_units.py`：策略、source、native final、完整音频测试。
- `C:\Users\zxh\Videos\测试11.MP3`：pz tail/final 回归。
- `C:\Users\zxh\Videos\测试12.MP3`：漏词/裁剪修复回归。
- `C:\Users\zxh\Videos\测试8.MP3`：partial 假句号观察样本。