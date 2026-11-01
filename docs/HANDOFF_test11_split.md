# Handoff: test11/test12 句末切分与音频裁剪

> **已被取代**：当前计划和会话状态见 `docs/PLAN_SEMANTIC_BOUNDARY.md` 与 `docs/SESSION_NOTES.md`。本文件保留作 test11/test12 的原始排查记录。

更新时间：2026-10-31 17:05 左右（Asia/Shanghai）
工作目录：`D:\AGI\hayamimi`

## 1. 当前目标

修复手动 pz 模式下：

- `测试11.MP3` 在“所有 / 内容”之间被切成两个 final；
- `测试12.MP3` 的“他认为，在此之前需要改变”被音频裁剪丢失。

最终方案：**移除音频裁剪，保留语义 flush + 文本 tail。**

用户期望的正常逻辑：

```text
如何。          -> 一个 final
以上呢就是...   -> tail/partial，和下一段的“内容”一起继续判断
```

不应该出现：

```text
...今天视频的所有。   -> final
内容。                 -> 下一个 final
```

## 2. 当前仓库 / 运行状态

- Git HEAD：`02de09a docs: fix 0.4.0 subtitle window usage notes`
- 未提交修改：
  - `README.en.md`
  - `scripts/realtime_transcribe.py`
  - `tests/test_units.py`
- 不提交、不推送。
- 当前测试结果：`235 passed, 2 skipped, 5 warnings`
- 当前 serve 进程（2026-10-31 17:02 启动）：
  ```text
  python scripts/realtime_transcribe.py --serve 8833 --input speaker --mode single --model pz
  ```
  HTTP 200，桌面窗可连接。
- 当前代码已经回退了错误的 `pending-period` guard 和临时 `HAYAMIMI_DEBUG_SPLIT` 日志。
- `SPLIT_END_TOLERANCE=1` 保留，用于 `is_flushed()` 的 1-sample 匹配。
- `trim_if_flushed()` 已彻底移除，音频段不再裁剪；`test11`、`test12` 和全部 `Videos` 测试已完成回归。

## 3. 已确认的根因：`is_flushed()` off-by-one

调试输出（未加 guard、当前回退后的路径）：

```text
SHOULD r=True audio=7.68
ARM audio=7.68 end=122879
IS_FLUSHED end=122880 -> False armed=122879
```

`vad.flush()` 实际产生的段尾是 `122880`，但 `SemanticSplitter.arm()` 记录的是 `122879`，相差 **1 个 sample**（16kHz 下 0.0625ms）。

因此：

- `should_flush()` 已经返回 `True`；
- `vad.flush()` 确实关闭了段；
- 但 `drain_segments()` 里 `was_flushed = splitter.is_flushed(seg_end)` 得到 `False`；
- `_split_sentence_tail()` 只在 `was_flushed=True` 时执行；
- 所以“如何。以上……所有。”整段作为一个 final 发布；
- “内容。”进入下一个 VAD 段。

日志中没有 `[tail] hold`，正是因为 `was_flushed=False`。

## 4. 之前为什么“临时修好”过

曾经加过一个错误 guard：

```python
stale_period = text[cut] in "。.．"
if len(tail) > (...) and (not same_candidate or stale_period):
    # reject
```

它不是修复根因，而是让 pending period 的长尾候选不再通过，从而完全不触发 semantic flush：

```text
flush=0
VAD 自然一段 -> “所有内容。”
```

这看起来“好了”，实际上只是绕开了 semantic flush，并且也阻止了“如何。”成为独立的 final。该 guard 已按要求回退。

## 5. 历史方案：1-sample 容差（最终已保留）

> 本节记录排查过程中的 off-by-one 修复；最终又进一步移除了整个音频裁剪路径，见第 13 节。当前没有 `trim_if_flushed()`。

目标文件：`scripts/realtime_transcribe.py`

### 5.1 增加容差常量

在 `SemanticSplitter` 相关常量附近增加：

```python
SPLIT_END_TOLERANCE = 1  # VAD flush may end one sample past current_segment length
```

### 5.2 修改 `is_flushed()`

当前代码约在 `scripts/realtime_transcribe.py:939`：

```python
def is_flushed(self, seg_end: int) -> bool:
    """True when this segment is the one armed for a semantic flush."""
    return self._flushed_end is not None and seg_end == self._flushed_end
```

改为容差匹配：

```python
def is_flushed(self, seg_end: int) -> bool:
    """True when this segment is the one armed for a semantic flush.

    VAD flush can end one sample past the captured current_segment length;
    treat that one-sample boundary jitter as the same segment.
    """
    return (self._flushed_end is not None
            and abs(seg_end - self._flushed_end) <= SPLIT_END_TOLERANCE)
```

### 5.3 修改 `trim_if_flushed()`

当前代码约在 `scripts/realtime_transcribe.py:943`：

```python
if self._flushed_end is None or seg_end != self._flushed_end:
    return samples
self._flushed_end = None
```

改为同样容差：

```python
if (self._flushed_end is None
        or abs(seg_end - self._flushed_end) > SPLIT_END_TOLERANCE):
    return samples
self._flushed_end = None
```

### 5.4 不要做这些

- 不要加回 `stale_period` guard。
- 不要加回 `HAYAMIMI_DEBUG_SPLIT` 日志。
- 不要修改 `_publish_final()` 的单 final 行为。
- 不要把 `lookahead` 改成 per-model。
- 不要改 `max_speech=20`。
- 不要为 Omni 增加 live 切句测试。

## 6. 测试更新

目标文件：`tests/test_units.py`

### 6.1 现有测试要改

约在 `tests/test_units.py:1276`：

```python
splitter.arm(11.0, 100)
assert splitter.is_flushed(100)
assert not splitter.is_flushed(101)
```

改为：

```python
splitter.arm(11.0, 100)
assert splitter.is_flushed(100)
assert splitter.is_flushed(101)   # one-sample VAD flush jitter
assert not splitter.is_flushed(102)
```

### 6.2 增加 trim 容差测试

使用现有 `trim_if_flushed` 测试模式，断言 `arm(..., n)` 后用 `n + 1` 仍能裁/处理，`n + 2` 不匹配。

### 6.3 保持现有 final 测试

- `test_natural_vad_segment_publishes_one_final`
- `test_english_natural_vad_segment_publishes_one_final`
- `test_english_semantic_flush_publishes_one_native_final`
- `test_v3_semantic_flush_publishes_one_native_final`
- `test_sensevoice_manual_zh_publishes_one_native_final`
- `test_pz_semantic_flush_still_holds_last_sentence`
- `test_flush_sentence_tail_publishes_and_clears_once`

## 7. 验证 test11

### 7.1 先转 wav（如果还没转）

```powershell
ffmpeg -hide_banner -loglevel error -y -i 'C:\Users\zxh\Videos\测试11.MP3' -ac 1 -ar 16000 -c:a pcm_s16le 'C:\Users\zxh\AppData\Local\Temp\opencode\test11.wav'
```

### 7.2 预期修复后的服务端路径

手动 pz，`--boundary-stats`：

```text
[zh/pz] ...cover能力如何。 (seg≈...)
[tail] hold '以上呢就是今天视频的所有'
[final] ...cover能力如何。
...
[zh/pz] 内容。
[final] ...以上呢就是今天视频的所有内容。
```

关键是：

- `如何。` 之后成为独立 final；
- `以上……所有` 进入 tail；
- 下一段“内容”合并后 final；
- 不出现“所有。/内容。”；
- 不重复、不截断。

### 7.3 检查 `[boundary-stats]`

- `flush` 应至少为 1（semantic flush 真正匹配）；
- `stale_tail` 不应阻止这次“如何”边界；
- 不应再出现 `was_flushed=False` 的 off-by-one。

## 8. 检查 `C:\Users\zxh\Videos` 下测试音频

当前目录中的测试音频：

```text
测试11.MP3
测试10.MP3
测试9.MP3
测试8.MP3
测试7.MP3
测试6.MP3
测试5.MP3
测试4.MP3
测试3.MP3
测试2.MP3
测试.MP3
9月26日(1).MP3
deramyfish1_audio.mp3
8月7日 (1)(5).MP3
8月7日 (1)(4).MP3
8月7日 (1)(3).MP3
8月7日 (1)(2).MP3
8月7日 (1)(1).MP3
8月7日 (1).MP3
```

建议顺序：

1. 先 `测试11`，确认 off-by-one 修复；
2. 再 `测试7` 到 `测试10`，回归之前的长段 / pz 行为；
3. 最后再考虑 `测试` 到 `测试6`；
4. 统一使用手动 pz，`--no-realtime`/`--wav`，开 `--boundary-stats`；
5. 每个文件至少检查：
   - final 数量；
   - `[tail] hold/merge`；
   - 是否出现“所有。/内容。”；
   - 是否有重复/截断；
   - `flush` / `stale_tail` 计数；
   - `测试8/9` 的 `max_speech=20` 是否仍正常。

## 9. 验证命令

```powershell
cd D:\AGI\hayamimi
& '.\.venv\Scripts\python.exe' -m pytest -q
& '.\.venv\Scripts\python.exe' -m py_compile scripts\realtime_transcribe.py tests\test_units.py
git diff --check
```

预期：

```text
236 passed, 2 skipped
```

如果新增 trim 容差测试，数量会增加；以实际为准。

## 10. 重启 serve

修复后必须重启，当前进程不会自动加载：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'realtime_transcribe' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```

然后启动：

```powershell
$p = Start-Process -FilePath 'D:\AGI\hayamimi\.venv\Scripts\python.exe' `
  -ArgumentList @('scripts\realtime_transcribe.py','--serve','8833','--input','speaker','--mode','single','--model','pz') `
  -WorkingDirectory 'D:\AGI\hayamimi' `
  -RedirectStandardOutput 'C:\Users\zxh\AppData\Local\Temp\hayamimi-serve.log' `
  -RedirectStandardError 'C:\Users\zxh\AppData\Local\Temp\hayamimi-serve.err.log' `
  -WindowStyle Hidden -PassThru
$p.Id
```

确认：

```powershell
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8833/' -TimeoutSec 5).StatusCode
```

## 11. 重要判断记录

不要再被这些现象误导：

- 服务器日志里没有 `[tail] hold`，说明 `was_flushed=False`，不是 tail 策略工作后失败。
- `vad.flush()` 不是按文本位置切；它关闭当前段。文本边界只影响 `arm` / `is_flushed` / 文本 tail。
- 回退 guard 后旧问题复现，是预期的。
- `test11` 的 8.109–8.644s 确实有约 0.534s 低能量段，但根因不止是 `min_silence`；语义 flush 已触发，只是 1-sample 匹配失败。
- `test12` 的漏词来自 `trim_if_flushed()` 裁掉 1.93s，而下一段只补回 1s preroll；不是 tail 文本逻辑本身丢失。
- 当前代码已移除音频裁剪；`test8` 仍有一个自然 VAD 边界切断“官方展示的例子”的 ASR 变体，需另行判断，不是本次 trim 问题。

## 12. 不要提交

当前所有修改保持未提交。除非用户明确要求，不要 commit/push。

## 13. 最终执行结果（2026-10-31 17:05）

### 本地提交

```text
58b71c1 fix(subtitle): stabilize semantic boundaries and sentence finals
147ce88 fix(subtitle): remove audio trimming from semantic flush
```

`docs/HANDOFF_test11_split.md` 仍是未跟踪文件，未提交。

### 代码

- 已移除 `drain_segments()` 中的 `trim_if_flushed()` 调用。
- 已删除 `SemanticSplitter.trim_if_flushed()`、`_boundary_ratio` 和 `TAIL_SCAN_S` / `TRIM_SEARCH_S` / `DIP_*` 常量。
- 保留 `should_flush()` / `arm()` / `is_flushed()` / `SPLIT_END_TOLERANCE=1`。
- 保留 `vad.flush()`、`_split_sentence_tail()`、`SentenceState`、`AudioHistory`。
- ASR 始终接收完整 VAD 段；不再有 trim 后的 1s 桥接。

### 测试

```text
235 passed, 2 skipped, 5 warnings
```

新增 `test_semantic_flush_passes_full_vad_audio_to_asr`，断言 semantic flush 段传给 ASR 的样本长度等于完整 VAD 段。

### test11

```text
[tail] hold '以上呢就是今天视频的所有'
[final] seq=1 text=大家可以通过这样的啊测试感受一下cover能力如何。
[tail] merge '以上呢就是今天视频的所有'
[final] seq=2 text=以上呢就是今天视频的所有内容。
...
[boundary-stats] ... flush=1 finals=5 ...
```

无丢词，无重复；“如何。”与“所有内容。”符合预期。

### test12

```text
[tail] hold '他认为，在此之前需要改变'
[final] seq=3 text=...其公司目前最强的内部模型。
[tail] merge '他认为，在此之前需要改变'
[final] seq=4 text=他认为，在此之前需要改变互联网整体的网络防御方式，并建立生物威胁防护层。
```

这是本次漏词问题的修复目标，已通过。

### Videos 回归

全部 `测试*.MP3` 已用手动 pz 跑过：

- `测试7`：1 final，正常。
- `测试8`：12 finals；有一处自然 VAD 边界落在“官方展示的例子”中间，ASR 输出为“官方展 / 这个例子”，缺少“示的”。这是自然 VAD/ASR 边界变体，不是音频被 trim；待另行判断。
- `测试9`：8 个英文 final，正常。
- `测试10`：4 finals，正常。
- `测试` / `测试2` / `测试6`：tail hold/merge 正常；`测试2`、`测试6` 已包含“他认为，在此之前……”。
- `测试3` / `测试4`：各 2 finals，正常。
- `测试5`：末尾 `open a。` 仍是 pz 识别片段，不是本次改动引入。

### serve

新进程已启动，HTTP 200：

```text
python scripts\realtime_transcribe.py --serve 8833 --input speaker --mode single --model pz
```

日志：

```text
C:\Users\zxh\AppData\Local\Temp\hayamimi-serve.log
C:\Users\zxh\AppData\Local\Temp\hayamimi-serve.err.log
```