---
title: 音频切点校准实现代理提示词
date: 2026-06-07
status: archived
audience: both
---

# 音频切点校准实现代理提示词

> 历史代理任务记录；文中代码和路径对应 `legacy` 分支。

## 共同约束

给每个实现代理都附上这段共同约束：

```text
你在“AI 视频剪辑工具”的音频切点校准实验框架中工作。

目标：实现一个可测试、可对比的切点策略。不要做完整 App，不要接 Gemini，不要做 UI。

必须遵守：
1. 先写测试，再写实现。
2. AI 不生成时间戳；策略只能基于 transcript token、RMS frame、VAD interval 微调边界。
3. 找不到可靠边界时，必须保守回退，不要硬切。
4. 所有输出边界必须 clamp 到 `duration_ms`。
5. `selected_segment_ids` 包含未知 ID 时必须报错。
6. token 必须按时间排序后再作为边界。
7. 输出必须保留 `source_segment_ids`、`adjustment_reason`、`confidence`。
8. 不要修改其他代理负责的策略文件范围，避免冲突。
9. 运行并报告测试命令和结果。

参考文档：
- docs/specs/audio-cutpoint-mvp-design.md
- docs/specs/audio-cutpoint-tdd-and-evaluation-plan.md
- docs/specs/audio-cutpoint-handoff.md
```

## 代理 A：Token Padding Baseline

```text
你负责方案 A：Token Padding Baseline。

任务目标：
实现一个不依赖音频的 baseline 策略 `token_padding`。它只使用 `TranscriptSegment` / `TranscriptToken` 时间戳，把用户确认保留的片段转成 `ClipPlan`。它是 B/C 的 fallback，所以必须稳定、确定、容易解释。

文件范围：
- src/cutpoint_lab/models.py
- src/cutpoint_lab/io.py
- src/cutpoint_lab/strategies.py 中 token padding 相关部分
- tests/test_cutpoint_*.py 中 A 方案和公共模型测试
- examples/ 中必要的小型 JSON fixture

不要修改：
- RMS 策略实现
- VAD 策略实现
- 视频导出代码
- UI 或 Gemini 相关代码

TDD 测试必须覆盖：
1. 正常 transcript JSON 可加载。
2. `selected_segment_ids` 包含未知 ID 时抛错。
3. token 时间乱序时按时间排序。
4. 无效 token 被过滤。
5. 有 token 时使用首/末 token 加 padding。
6. 无 token 时回退 segment 边界加 padding。
7. 相邻已选片段 gap 小于 `merge_gap_ms` 时合并。
8. 相邻已选片段 gap 大于 `merge_gap_ms` 时分开。
9. 片头/片尾 clamp。
10. 输出保留 `source_segment_ids`、reason、confidence。

默认参数：
- `pre_roll_ms=160`
- `post_roll_ms=240`
- `merge_gap_ms=500`
- `start_guard_ms=60`
- `end_guard_ms=80`

验收标准：
- `python -m unittest discover -s tests` 通过。
- `token_padding` 不需要任何音频/VAD 输入也能输出 plan。
- 不存在静默漏掉 selected segment 的情况。

最终回复：
1. 修改了哪些文件。
2. 新增/更新了哪些测试。
3. 测试命令和结果。
4. 方案 A 的已知局限。
```

## 代理 B：RMS / Silence Low-Energy Snap

```text
你负责方案 B：RMS / Silence Low-Energy Snap。

任务目标：
在方案 A 的 fallback 基础上，实现 `rms_snap`。它读取音频 RMS frames，在 token 边界附近寻找低能量/静音 gap，把 start/end 吸附到更自然的气口或停顿处。

文件范围：
- src/cutpoint_lab/features.py 中 RMS frame / wav 内容验证相关部分
- src/cutpoint_lab/strategies.py 中 RMS 策略相关部分
- tests/test_cutpoint_*.py 中 B 方案测试
- examples/ 中必要的小型 RMS fixture

不要修改：
- token_padding 的公共语义，除非发现阻塞 bug 并明确说明
- VAD 策略实现
- UI、Gemini、视频导出

TDD 测试必须覆盖：
1. 边界附近有强低能量 gap，成功吸附。
2. 无低能量 gap，回退 token padding。
3. RMS 动态范围小于 10dB，回退 token padding。
4. gap 在首/末 token 内，不接受。
5. 起点候选必须在首 token 前至少 60ms。
6. 终点候选必须在末 token 后至少 80ms。
7. RMS frames 缺失时 fallback。
8. 片头/片尾 clamp。
9. 输出 reason 区分 `snapped_to_rms_gap` 和 `rms_fallback_token_padding`。
10. CLI compare 能输出 `rms_snap` plan。

默认参数：
- `frame_ms=20`
- `noise_floor_percentile=10`
- `low_energy_margin_db=6`
- `dynamic_range_min_db=10`
- `min_gap_ms=120`
- `weak_gap_ms=80`
- 起点窗口：`-600ms / +200ms`
- 终点窗口：`-200ms / +800ms`

实现建议：
1. 不要用固定绝对 dB 阈值硬切。
2. 低能量 gap 只作为外边界吸附，不删除片段内部停顿。
3. 没有可靠 gap 时要保守回退。
4. 音频提取后要验证 wav 有内容，不要只看文件存在。

验收标准：
- `python -m unittest discover -s tests` 通过。
- 干净低能量 gap 能吸附。
- 连续语音/高噪声不会误吸附。

最终回复：
1. 修改了哪些文件。
2. 新增/更新了哪些测试。
3. 测试命令和结果。
4. RMS 方案适合/不适合的素材。
```

## 代理 C：VAD Interval Snap

```text
你负责方案 C：VAD Interval Snap。

任务目标：
实现 `vad_snap`。它读取 VAD speech intervals，推导 non-speech gaps，在 token 边界附近把 start/end 吸附到更自然的非语音边界。后续会接 Silero VAD，所以接口要保持简单清晰。

文件范围：
- src/cutpoint_lab/models.py 中 VAD 数据结构相关部分
- src/cutpoint_lab/io.py 中 VAD JSON 加载相关部分
- src/cutpoint_lab/strategies.py 中 VAD 策略相关部分
- tests/test_cutpoint_*.py 中 C 方案测试
- examples/ 中必要的小型 VAD fixture

不要修改：
- RMS 策略实现
- UI、Gemini、视频导出

TDD 测试必须覆盖：
1. 正常 VAD speech interval 能推导 non-speech gap。
2. 起点吸附到首 token 前的 non-speech gap。
3. 终点吸附到末 token 后的 non-speech gap。
4. VAD 缺失时 fallback。
5. VAD overlap 先合并。
6. VAD 抖动 gap 小于 80ms 时合并 speech。
7. gap 太短时 fallback。
8. 起点/终点 token guard。
9. VAD interval 越界时裁剪到 duration。
10. 无 duration 时不能错误推导尾部 gap。
11. 快速口播几乎无 gap 时 fallback。
12. CLI compare 能输出 `vad_snap` plan。

默认参数：
- `merge_speech_gap_ms=80`
- `min_gap_ms=120`
- `weak_gap_ms=80`
- `pre_roll_ms=160`
- `post_roll_ms=240`
- 起点窗口：`-600ms / +200ms`
- 终点窗口：`-200ms / +800ms`
- `start_guard_ms=60`
- `end_guard_ms=80`

实现建议：
1. 先 normalize VAD intervals：排序、裁剪、合并 overlap/抖动。
2. 从 speech intervals 的补集推导 non-speech gaps。
3. VAD gap 必须通过 token guard。
4. 找不到可靠 gap 时回退 token_padding。
5. 输出 reason 区分 `snapped_to_vad_gap` 和 `vad_fallback_token_padding`。

验收标准：
- `python -m unittest discover -s tests` 通过。
- VAD 正常时能吸附，VAD 不可靠时保守回退。
- 输出 range 不越界、不反转、不吃 token。

最终回复：
1. 修改了哪些文件。
2. 新增/更新了哪些测试。
3. 测试命令和结果。
4. VAD 方案接 Silero VAD 前还缺什么。
```

## 代理 D：效果评估与提测包

```text
你负责三方案效果对比框架，不负责实现 A/B/C 算法。

任务目标：
实现或设计一个 evaluator，把同一批样本分别跑 `token_padding`、`rms_snap`、`vad_snap`，输出自动指标、人工评分模板和提测包目录。

文件范围：
- src/cutpoint_lab/evaluator.py 或等价评估模块
- src/cutpoint_lab/cli.py 中 eval/compare 相关命令
- tests/test_evaluator_*.py
- docs/specs/audio-cutpoint-tdd-and-evaluation-plan.md 如需补充

不要修改：
- A/B/C 策略核心算法

必须支持的输入：
- transcript JSON
- optional audio wav / RMS frames
- optional VAD JSON
- optional gold boundary JSON

必须输出：
- compare.json
- summary.csv
- notes.md 人工评分模板

自动指标至少包括：
1. range_count
2. total_duration_ms
3. fallback_count
4. snap_hit_rate
5. delta_start_ms / delta_end_ms
6. token_chop_risk
7. boundary_risk_count
8. gold_error_start_ms / gold_error_end_ms，如果有 gold

TDD 测试必须覆盖：
1. 三策略输出能汇总成 compare.json。
2. summary.csv 字段完整。
3. gold boundary 缺失时不崩溃。
4. 单个策略失败时记录失败，不影响其他策略结果。
5. notes.md 模板包含人工评分维度。

验收标准：
- evaluator 不判断最终胜负，只输出可审查数据。
- 提测包目录结构稳定：
  outputs/eval/YYYYMMDD-HHMM/
    compare.json
    summary.csv
    notes.md

最终回复：
1. 修改了哪些文件。
2. 新增/更新了哪些测试。
3. 测试命令和结果。
4. 如何拿真实视频跑一轮评估。
```
