# Semantic Boundary Plan

> 项目根目录：`D:\AGI\hayamimi`  
> 最近更新：2026-11-01  
> 状态：主体已实现，按来源的策略仍在工作区，尚未提交。

## Objective

让语义断句按 ASR 来源和句末标点类型使用不同的边界策略，同时保持：

- `lookahead = 1.0s`；
- 不再对 semantic-flush 音频做裁剪；
- `pz/rz` 使用文本 tail；
- `sv/v2/v3` native punctuation，直接 final；
- `omni` 完全禁用 live semantic split。

## Confirmed Constraints

- 所有模型共用 `lookahead=1.0`，不按模型调整 lookahead。
- 不再恢复 `trim_if_flushed()` 或任何 audio trim。
- `pz/rz` 的最后一句进入 `SentenceState.tail`，由下一段合并。
- `sv/v2/v3` 已经属于 `_NATIVE_PUNCT_SOURCES`，final 路径不执行 `_split_sentence_tail()`。
- `sv` 保留 semantic flush，但没有文本 tail。
- `v2/v3` 是英语/欧洲语言 native punctuation，不使用 CJK tail 分支。
- `omni` 不触发 live semantic split，也不建立 tail；只走自然 VAD final。
- 不新增 Omni live-split 测试。

## Boundary Policy

`tail` 指最后一个有效句末标点之后的 fresh text。判断使用 Python 字符长度；超过 max 即 `stale_tail`。

| source | semantic flush | 文本 tail | `。/.` max | `？/！/?/!` max |
|---|---|---|---|---|
| `pz` | 是 | 是 | 8 CJK / 20 Latin | 8 / 20 |
| `rz` | 是 | 是 | 8 / 20 | 12 / 24 |
| `sv` | 是 | 否，直接 final | 8 / 20 | 12 / 24 |
| `v2` | 是 | 否，直接 final | 仅 Latin 20 | 仅 Latin 24 |
| `v3` | 是 | 否，直接 final | 仅 Latin 20 | 仅 Latin 24 |
| `omni` | 否 | 否 | 不适用 | 不适用 |

最小 fresh tail：

```text
CJK   >= 4 chars
Latin >= 2 chars
```

CJK 判定使用：

```python
re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", tail)
```

即日文假名 + 汉字；纯 Latin/非 CJK 使用 Latin 分支。  
当前 Hangul 仍会落到 Latin 分支，这是一个已知待观察点。

## Design / Architecture

### Source propagation

`RoutedASR.partial()` 在每次返回 draft 时写入：

```python
self.last_partial_source = "pz" / "rz" / "sv" / "v2" / "v3" / "omni"
```

`run_stream()` 在调用 splitter 时传入：

```python
splitter.should_flush(text, speech_s, audio_pos, source=partial_source)
```

### Splitter

`SemanticSplitter.should_flush()` 增加 `source` 参数，并使用 `BOUNDARY_POLICIES` 选择：

- `allow_live`；
- `use_cjk`；
- period / strong 的 CJK / Latin 最大 tail。

`same_candidate` 逻辑保持不变：lookahead 等待期间同一候选的 tail 可以增长，不在中途因超过 max 拒绝。

### Final path

`drain_segments()` 使用：

```python
_NO_TAIL_SOURCES = _NATIVE_PUNCT_SOURCES | {"omni"}
```

`sv/v2/v3/omni` 不建立新的文本 tail；`sv/v2/v3` 仍可由 semantic flush 触发 direct final。`omni` 由 `allow_live=False` 阻止 live split。

## Implementation Phases

1. **已完成**：移除音频裁剪，保留文本 tail。
2. **已完成（工作区，未提交）**：source propagation。
3. **已完成（工作区，未提交）**：`BOUNDARY_POLICIES` 和 `should_flush(source=...)`。
4. **已完成（工作区，未提交）**：`sv/v2/v3` direct final、`omni` live split 禁用。
5. **待办**：审查并提交工作区改动。
6. **待办**：根据真实模型音频决定是否需要调整强标点阈值和 Hangul 分支。

## Tests and QA

已执行：

```text
27 passed, 94 deselected   # targeted test_units.py
236 passed, 2 skipped      # full pytest -q
```

核心用例：

- `test_boundary_policy_is_source_aware`
- `test_flush_waits_for_right_context_when_configured`
- `test_semantic_splitter_boundary_counters`
- `test_flush_refuses_stale_boundary_with_long_tail`
- `test_flush_allows_fresh_boundary_within_tail_window`
- `test_pz_semantic_flush_still_holds_last_sentence`
- `test_semantic_flush_passes_full_vad_audio_to_asr`
- `test_partial_forced_lang_routes_directly_without_lid_or_sv_probe`

实际音频已验证：

- `pz`：`测试11`、`测试12`，final/tail 正常；
- `pz`：未恢复 audio trim；
- `v2/v3`：英语测试音频走 native direct final；
- `sv`：semantic flush 生效，无 tail；`测试11` 会出现 native direct final 的独立 `内容？`；
- `omni`：`live_disabled` 计数增长，`flush=0`。

## Risks and Mitigations

- **v2/v3 CJK**：策略显式 `use_cjk=False`，正常欧洲语言不会进入 CJK 分支。
- **sv 直接 final**：不合并 tail，因此可能出现 `所有。/内容？`；这是 native direct final 语义，不是 pz 回归。
- **Hangul**：当前按 Latin 统计；若韩文语义边界表现异常，单独增加 Hangul 分支。
- **强标点 12/24**：比旧全局 8/20 略宽，但仍远小于 32/48；需要用真实 rz/sv 音频继续观察。
- **partial 假句号**：`测试8` 的“进行的。后训练”是 draft CT 假句号，最终 full-context 会消失；当前决定不改。

## Files

- `scripts/asr_engine.py`：`last_partial_source`。
- `scripts/realtime_transcribe.py`：`BoundaryPolicy`、source-aware `should_flush()`、`_NO_TAIL_SOURCES`、run_stream source 传递。
- `tests/test_units.py`：策略、source、native final、完整音频测试。
- `docs/SESSION_NOTES.md`：会话状态。
- `docs/HANDOFF_test11_split.md`：历史排查记录，已被本计划取代，保留作原始证据。

## Acceptance Criteria

- `pz` 不再因 audio trim 丢失句子尾音。
- `test11` 保持“如何。”和“所有内容。”。
- `test12` 保持“他认为，在此之前需要改变……”。
- `v2/v3` 不进入 CJK tail。
- `sv` semantic flush 正常工作且不建立文本 tail。
- `omni` `should_flush()` 永不返回 true。
- 完整 pytest 无新增失败。

## Next Action

1. 审查并提交工作区的 source-aware policy。
2. 如需要，针对 `rz` 日语和 `sv` 韩文补充真实音频采样，再决定阈值微调。
3. 更新 `docs/SESSION_NOTES.md` 的提交哈希和最终状态。